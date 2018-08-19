#!/usr/bin/env python3
import pytrellis
import database
import argparse
import json
from os import path

location_types = dict()
type_at_location = dict()
tiletype_names = dict()

parser = argparse.ArgumentParser(description="import ECP5 routing and bels from Project Trellis")
parser.add_argument("device", type=str, help="target device")
parser.add_argument("-p", "--constids", type=str, help="path to constids.inc")
args = parser.parse_args()


def is_global(loc):
    return loc.x == -2 and loc.y == -2


# Get the index for a tiletype
def get_tiletype_index(name):
    if name in tiletype_names:
        return tiletype_names[name]
    idx = len(tiletype_names)
    tiletype_names[name] = idx
    return idx


constids = dict()


class BinaryBlobAssembler:
    def l(self, name, ltype = None, export = False):
        if ltype is None:
            print("label %s" % (name,))
        else:
            print("label %s %s" % (name, ltype))

    def r(self, name, comment):
        if comment is None:
            print("ref %s" % (name,))
        else:
            print("ref %s %s" % (name, comment))

    def s(self, s, comment):
        assert "|" not in s
        print("str |%s| %s" % (s, comment))

    def u8(self, v, comment):
        if comment is None:
            print("u8 %d" % (v,))
        else:
            print("u8 %d %s" % (v, comment))

    def u16(self, v, comment):
        if comment is None:
            print("u16 %d" % (v,))
        else:
            print("u16 %d %s" % (v, comment))

    def u32(self, v, comment):
        if comment is None:
            print("u32 %d" % (v,))
        else:
            print("u32 %d %s" % (v, comment))

    def pre(self, s):
        print("pre %s" % s)

    def post(self, s):
        print("post %s" % s)

    def push(self, name):
        print("push %s" % name)

    def pop(self):
        print("pop")

def get_bel_index(ddrg, loc, name):
    loctype = ddrg.locationTypes[ddrg.typeAtLocation[loc]]
    idx = 0
    for bel in loctype.bels:
        if ddrg.to_str(bel.name) == name:
            return idx
        idx += 1
    assert loc.y == max_row # Only missing IO should be special pins at bottom of device
    return None


packages = {}
pindata = []

def process_pio_db(ddrg, device):
    piofile = path.join(database.get_db_root(), "ECP5", dev_names[device], "iodb.json")
    with open(piofile, 'r') as f:
        piodb = json.load(f)
        for pkgname, pkgdata in sorted(piodb["packages"].items()):
            pins = []
            for name, pinloc in sorted(pkgdata.items()):
                x = pinloc["col"]
                y = pinloc["row"]
                loc = pytrellis.Location(x, y)
                pio = "PIO" + pinloc["pio"]
                bel_idx = get_bel_index(ddrg, loc, pio)
                if bel_idx is not None:
                    pins.append((name, loc, bel_idx))
            packages[pkgname] = pins
        for metaitem in piodb["pio_metadata"]:
            x = metaitem["col"]
            y = metaitem["row"]
            loc = pytrellis.Location(x, y)
            pio = "PIO" + metaitem["pio"]
            bank = metaitem["bank"]
            if "function" in metaitem:
                pinfunc = metaitem["function"]
            else:
                pinfunc = None
            bel_idx = get_bel_index(ddrg, loc, pio)
            if bel_idx is not None:
                pindata.append((loc, bel_idx, bank, pinfunc))

global_data = {}
quadrants = ["UL", "UR", "LL", "LR"]
def process_loc_globals(chip):
    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            quad = chip.global_data.get_quadrant(y, x)
            tapdrv = chip.global_data.get_tap_driver(y, x)
            global_data[x, y] = (quadrants.index(quad), int(tapdrv.dir), tapdrv.col)

def get_wire_type(name):
    if "H00" in name or "V00" in name:
        return "X0"
    if "H01" in name or "V01" in name:
        return "X1"
    if "H02" in name or "V02" in name:
        return "X2"
    if "H06" in name or "V06" in name:
        return "X6"
    if "_SLICE" in name or "_EBR" in name:
        return "SLICE"
    return "LOCAL"

def get_pip_delay(wire_from, wire_to):
    # ECP5 timings WIP!!!
    type_from = get_wire_type(wire_from)
    type_to = get_wire_type(wire_to)
    if type_from == "X2" and type_to == "X2":
        return 170
    if type_from == "SLICE" or type_to == "SLICE":
        return 205
    if type_from in ("LOCAL", "X0") and type_to in ("X1", "X2", "X6"):
        return 90
    if type_from == "X6" or type_to == "X6":
        return 200
    if type_from in ("X1", "X2", "X6") and type_to in ("LOCAL", "X0"):
        return 90
    return 100



def write_database(dev_name, chip, ddrg, endianness):
    def write_loc(loc, sym_name):
        bba.u16(loc.x, "%s.x" % sym_name)
        bba.u16(loc.y, "%s.y" % sym_name)

    loctypes = list([_.key() for _ in ddrg.locationTypes])
    loc_with_type = {}
    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            loc_with_type[loctypes.index(ddrg.typeAtLocation[pytrellis.Location(x, y)])] = (x, y)

    def get_wire_name(arc_loctype, rel, idx):
        loc = loc_with_type[arc_loctype]
        lt = ddrg.typeAtLocation[pytrellis.Location(loc[0] + rel.x, loc[1] + rel.y)]
        wire = ddrg.locationTypes[lt].wires[idx]
        return ddrg.to_str(wire.name)

    bba = BinaryBlobAssembler()
    bba.pre('#include "nextpnr.h"')
    bba.pre('NEXTPNR_NAMESPACE_BEGIN')
    bba.post('NEXTPNR_NAMESPACE_END')
    bba.push("chipdb_blob_%s" % dev_name)
    bba.r("chip_info", "chip_info")


    for idx in range(len(loctypes)):
        loctype = ddrg.locationTypes[loctypes[idx]]
        if len(loctype.arcs) > 0:
            bba.l("loc%d_pips" % idx, "PipInfoPOD")
            for arc in loctype.arcs:
                write_loc(arc.srcWire.rel, "src")
                write_loc(arc.sinkWire.rel, "dst")
                bba.u32(arc.srcWire.id, "src_idx")
                bba.u32(arc.sinkWire.id, "dst_idx")
                bba.u32(get_pip_delay(get_wire_name(idx, arc.srcWire.rel, arc.srcWire.id), get_wire_name(idx, arc.sinkWire.rel, arc.sinkWire.id)), "delay")  # TODO:delay
                bba.u16(get_tiletype_index(ddrg.to_str(arc.tiletype)), "tile_type")
                bba.u8(int(arc.cls), "pip_type")
                bba.u8(0, "padding")
        if len(loctype.wires) > 0:
            for wire_idx in range(len(loctype.wires)):
                wire = loctype.wires[wire_idx]
                if len(wire.arcsDownhill) > 0:
                    bba.l("loc%d_wire%d_downpips" % (idx, wire_idx), "PipLocatorPOD")
                    for dp in wire.arcsDownhill:
                        write_loc(dp.rel, "rel_loc")
                        bba.u32(dp.id, "index")
                if len(wire.arcsUphill) > 0:
                    bba.l("loc%d_wire%d_uppips" % (idx, wire_idx), "PipLocatorPOD")
                    for up in wire.arcsUphill:
                        write_loc(up.rel, "rel_loc")
                        bba.u32(up.id, "index")
                if len(wire.belPins) > 0:
                    bba.l("loc%d_wire%d_belpins" % (idx, wire_idx), "BelPortPOD")
                    for bp in wire.belPins:
                        write_loc(bp.bel.rel, "rel_bel_loc")
                        bba.u32(bp.bel.id, "bel_index")
                        bba.u32(constids[ddrg.to_str(bp.pin)], "port")
            bba.l("loc%d_wires" % idx, "WireInfoPOD")
            for wire_idx in range(len(loctype.wires)):
                wire = loctype.wires[wire_idx]
                bba.s(ddrg.to_str(wire.name), "name")
                bba.u32(len(wire.arcsUphill), "num_uphill")
                bba.u32(len(wire.arcsDownhill), "num_downhill")
                bba.r("loc%d_wire%d_uppips" % (idx, wire_idx) if len(wire.arcsUphill) > 0 else None, "pips_uphill")
                bba.r("loc%d_wire%d_downpips" % (idx, wire_idx) if len(wire.arcsDownhill) > 0 else None, "pips_downhill")
                bba.u32(len(wire.belPins), "num_bel_pins")
                bba.r("loc%d_wire%d_belpins" % (idx, wire_idx) if len(wire.belPins) > 0 else None, "bel_pins")

        if len(loctype.bels) > 0:
            for bel_idx in range(len(loctype.bels)):
                bel = loctype.bels[bel_idx]
                bba.l("loc%d_bel%d_wires" % (idx, bel_idx), "BelWirePOD")
                for pin in bel.wires:
                    write_loc(pin.wire.rel, "rel_wire_loc")
                    bba.u32(pin.wire.id, "wire_index")
                    bba.u32(constids[ddrg.to_str(pin.pin)], "port")
                    bba.u32(int(pin.dir), "dir")
            bba.l("loc%d_bels" % idx, "BelInfoPOD")
            for bel_idx in range(len(loctype.bels)):
                bel = loctype.bels[bel_idx]
                bba.s(ddrg.to_str(bel.name), "name")
                bba.u32(constids[ddrg.to_str(bel.type)], "type")
                bba.u32(bel.z, "z")
                bba.u32(len(bel.wires), "num_bel_wires")
                bba.r("loc%d_bel%d_wires" % (idx, bel_idx), "bel_wires")

    bba.l("locations", "LocationTypePOD")
    for idx in range(len(loctypes)):
        loctype = ddrg.locationTypes[loctypes[idx]]
        bba.u32(len(loctype.bels), "num_bels")
        bba.u32(len(loctype.wires), "num_wires")
        bba.u32(len(loctype.arcs), "num_pips")
        bba.r("loc%d_bels" % idx if len(loctype.bels) > 0 else None, "bel_data")
        bba.r("loc%d_wires" % idx if len(loctype.wires) > 0 else None, "wire_data")
        bba.r("loc%d_pips" % idx if len(loctype.arcs) > 0 else None, "pips_data")

    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            bba.l("tile_info_%d_%d" % (x, y), "TileNamePOD")
            for tile in chip.get_tiles_by_position(y, x):
                bba.s(tile.info.name, "name")
                bba.u16(get_tiletype_index(tile.info.type), "type_idx")
                bba.u16(0, "padding")

    bba.l("tiles_info", "TileInfoPOD")
    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            bba.u32(len(chip.get_tiles_by_position(y, x)), "num_tiles")
            bba.r("tile_info_%d_%d" % (x, y), "tile_names")

    bba.l("location_types", "int32_t")
    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            bba.u32(loctypes.index(ddrg.typeAtLocation[pytrellis.Location(x, y)]), "loctype")

    bba.l("location_glbinfo", "GlobalInfoPOD")
    for y in range(0, max_row+1):
        for x in range(0, max_col+1):
            bba.u16(global_data[x, y][2], "tap_col")
            bba.u8(global_data[x, y][1], "tap_dir")
            bba.u8(global_data[x, y][0], "quad")

    for package, pkgdata in sorted(packages.items()):
        bba.l("package_data_%s" % package, "PackagePinPOD")
        for pin in pkgdata:
            name, loc, bel_idx = pin
            bba.s(name, "name")
            write_loc(loc, "abs_loc")
            bba.u32(bel_idx, "bel_index")

    bba.l("package_data", "PackageInfoPOD")
    for package, pkgdata in sorted(packages.items()):
        bba.s(package, "name")
        bba.u32(len(pkgdata), "num_pins")
        bba.r("package_data_%s" % package, "pin_data")

    bba.l("pio_info", "PIOInfoPOD")
    for pin in pindata:
        loc, bel_idx, bank, func = pin
        write_loc(loc, "abs_loc")
        bba.u32(bel_idx, "bel_index")
        if func is not None:
            bba.s(func, "function_name")
        else:
            bba.r(None, "function_name")
        bba.u16(bank, "bank")
        bba.u16(0, "padding")


    bba.l("tiletype_names", "RelPtr<char>")
    for tt, idx in sorted(tiletype_names.items(), key=lambda x: x[1]):
        bba.s(tt, "name")

    bba.l("chip_info")
    bba.u32(max_col + 1, "width")
    bba.u32(max_row + 1, "height")
    bba.u32((max_col + 1) * (max_row + 1), "num_tiles")
    bba.u32(len(location_types), "num_location_types")
    bba.u32(len(packages), "num_packages")
    bba.u32(len(pindata), "num_pios")

    bba.r("locations", "locations")
    bba.r("location_types", "location_type")
    bba.r("location_glbinfo", "location_glbinfo")
    bba.r("tiletype_names", "tiletype_names")
    bba.r("package_data", "package_info")
    bba.r("pio_info", "pio_info")
    bba.r("tiles_info", "tile_info")

    bba.pop()
    return bba

dev_names = {"25k": "LFE5U-25F", "45k": "LFE5U-45F", "85k": "LFE5U-85F"}

def main():
    global max_row, max_col
    pytrellis.load_database(database.get_db_root())
    args = parser.parse_args()

    # Read port pin file
    with open(args.constids) as f:
        for line in f:
            line = line.replace("(", " ")
            line = line.replace(")", " ")
            line = line.split()
            if len(line) == 0:
                continue
            assert len(line) == 2
            assert line[0] == "X"
            idx = len(constids) + 1
            constids[line[1]] = idx
    

    constids["SLICE"] = constids["TRELLIS_SLICE"]
    constids["PIO"] = constids["TRELLIS_IO"]

    # print("Initialising chip...")
    chip = pytrellis.Chip(dev_names[args.device])
    # print("Building routing graph...")
    ddrg = pytrellis.make_dedup_chipdb(chip)
    max_row = chip.get_max_row()
    max_col = chip.get_max_col()
    process_pio_db(ddrg, args.device)
    process_loc_globals(chip)
    # print("{} unique location types".format(len(ddrg.locationTypes)))
    bba = write_database(args.device, chip, ddrg, "le")



if __name__ == "__main__":
    main()
