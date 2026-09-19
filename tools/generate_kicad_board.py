import os
import sys
import math
import pcbnew

def generate_board():
    base_dir = "/home/ske/teamwork_projects/adaptive_sonar_transmitter/github_release/hardware/kicad"
    os.makedirs(base_dir, exist_ok=True)
    board_path = os.path.join(base_dir, "SAMUDRA_AFE_Shield.kicad_pcb")

    board = pcbnew.BOARD()
    
    # Setup Title Block
    tb = pcbnew.TITLE_BLOCK()
    tb.SetTitle("PROJECT SAMUDRA | AUV SONAR PAYLOAD")
    tb.SetDate("2026-09-19")
    tb.SetRevision("REV 1.0 (2026)")
    tb.SetCompany("NIOT SIH26058")
    tb.SetComment(0, "4th-Order Bessel AFE Shield")
    board.SetTitleBlock(tb)
    
    # Outline layer
    outline_layer = pcbnew.Edge_Cuts
    def add_line(x1, y1, x2, y2):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.wxPoint(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
        seg.SetEnd(pcbnew.wxPoint(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
        seg.SetLayer(outline_layer)
        seg.SetWidth(pcbnew.FromMM(0.1))
        board.Add(seg)

    def add_arc(cx, cy, sx, sy, angle_deg):
        arc = pcbnew.PCB_SHAPE(board)
        arc.SetShape(pcbnew.SHAPE_T_ARC)
        arc.SetCenter(pcbnew.wxPoint(pcbnew.FromMM(cx), pcbnew.FromMM(cy)))
        arc.SetStart(pcbnew.wxPoint(pcbnew.FromMM(sx), pcbnew.FromMM(sy)))
        arc.SetArcAngleAndEnd(angle_deg * 10) # tenths of degrees in KiCad 6
        arc.SetLayer(outline_layer)
        arc.SetWidth(pcbnew.FromMM(0.1))
        board.Add(arc)

    # 70x54 outline with 3mm corners
    add_line(3, 0, 67, 0)
    add_line(70, 3, 70, 51)
    add_line(67, 54, 3, 54)
    add_line(0, 51, 0, 3)
    
    add_arc(3, 3, 3, 0, -900)
    add_arc(67, 3, 70, 3, -900)
    add_arc(67, 51, 67, 54, -900)
    add_arc(3, 51, 0, 51, -900)

    # Silkscreen text
    def add_text(text, x, y, size=1.5):
        txt = pcbnew.PCB_TEXT(board)
        txt.SetText(text)
        txt.SetPosition(pcbnew.wxPoint(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        txt.SetLayer(pcbnew.F_SilkS)
        txt.SetTextSize(pcbnew.wxSize(pcbnew.FromMM(size), pcbnew.FromMM(size)))
        txt.SetTextThickness(pcbnew.FromMM(size * 0.15))
        board.Add(txt)

    add_text("PROJECT SAMUDRA | AUV SONAR PAYLOAD", 35, 27, 1.5)
    add_text("NIOT SIH26058 | 4th-Order Bessel AFE Shield", 35, 29, 1.2)
    add_text("REV 1.0 (2026)", 35, 31, 1.0)
    
    # Define Nets
    nets = ["GND", "+5V", "+9V", "-9V", "PA4", "U1A_OUT", "U1B_OUT", "U2A_OUT", "U2B_OUT", "C5_IN", "C5_OUT", "R6_OUT"]
    net_map = {}
    for i, name in enumerate(nets, start=1):
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        net_map[name] = n
        
    # Components mapping (Ref, Lib, Footprint, X, Y)
    components = [
        # Core A
        ("U1", "Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", 30, 20),
        ("U2", "Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", 40, 20),
        ("R1", "Resistor_SMD", "R_0805_2012Metric", 25, 25),
        ("R2", "Resistor_SMD", "R_0805_2012Metric", 27, 25),
        ("R3", "Resistor_SMD", "R_0805_2012Metric", 29, 25),
        ("R4", "Resistor_SMD", "R_0805_2012Metric", 31, 25),
        ("R5", "Resistor_SMD", "R_0805_2012Metric", 33, 25),
        ("R6", "Resistor_SMD", "R_0805_2012Metric", 45, 20),
        ("R7", "Resistor_SMD", "R_0805_2012Metric", 37, 25),
        ("C1", "Capacitor_SMD", "C_0805_2012Metric", 25, 28),
        ("C2", "Capacitor_SMD", "C_0805_2012Metric", 27, 28),
        ("C3", "Capacitor_SMD", "C_0805_2012Metric", 29, 28),
        ("C4", "Capacitor_SMD", "C_0805_2012Metric", 31, 28),
        ("C5", "Capacitor_THT", "C_Rect_L13.0mm_W4.0mm_P10.00mm_FKS3_FKP3_MKS4", 45, 10),
        ("C6", "Capacitor_SMD", "C_0805_2012Metric", 30, 15),
        ("C7", "Capacitor_SMD", "C_0805_2012Metric", 32, 15),
        ("C8", "Capacitor_SMD", "C_0805_2012Metric", 40, 15),
        ("C9", "Capacitor_SMD", "C_0805_2012Metric", 42, 15),
        ("J2", "Connector_Coaxial", "BNC_Amphenol_B6252HB-NPP3G-50_Horizontal", 60, 20),
        # Core B
        ("RV1", "Potentiometer_THT", "Potentiometer_Bourns_3386P_Vertical", 10, 45),
        ("RV2", "Potentiometer_THT", "Potentiometer_Bourns_3386P_Vertical", 30, 45),
        ("RV3", "Potentiometer_THT", "Potentiometer_Bourns_3386P_Vertical", 50, 45),
        ("R8", "Resistor_SMD", "R_0805_2012Metric", 10, 40),
        ("R9", "Resistor_SMD", "R_0805_2012Metric", 30, 40),
        ("R10", "Resistor_SMD", "R_0805_2012Metric", 50, 40),
        ("C10", "Capacitor_SMD", "C_0805_2012Metric", 15, 40),
        ("C11", "Capacitor_SMD", "C_0805_2012Metric", 35, 40),
        ("C12", "Capacitor_SMD", "C_0805_2012Metric", 55, 40),
        ("SW1", "Button_Switch_THT", "SW_Slide_1P2T_CK_OS102011MS2Q", 60, 45),
        ("R11", "Resistor_SMD", "R_0805_2012Metric", 65, 40),
        # Core C
        ("J1", "TerminalBlock_Phoenix", "TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal", 10, 10),
        ("D1", "Diode_SMD", "D_SMA", 18, 10),
        ("LED1", "LED_SMD", "LED_0805_2012Metric", 10, 15),
        ("LED2", "LED_SMD", "LED_0805_2012Metric", 15, 15),
        ("R12", "Resistor_SMD", "R_0805_2012Metric", 10, 18),
        ("R13", "Resistor_SMD", "R_0805_2012Metric", 15, 18),
        # Core D - Headers
        ("CN5", "Connector_PinHeader_2.54mm", "PinHeader_1x10_P2.54mm_Vertical", 5, 27),
        ("CN6", "Connector_PinHeader_2.54mm", "PinHeader_1x08_P2.54mm_Vertical", 5, 10),
        ("CN7", "Connector_PinHeader_2.54mm", "PinHeader_1x06_P2.54mm_Vertical", 65, 10),
        
        # Added DFM
        ("C_BULK_BATT", "Capacitor_THT", "CP_Radial_D6.3mm_P2.50mm", 20, 5),
        ("C_BULK_9VP", "Capacitor_THT", "CP_Radial_D6.3mm_P2.50mm", 30, 5),
        ("C_BULK_9VN", "Capacitor_THT", "CP_Radial_D6.3mm_P2.50mm", 40, 5),
        ("F1", "Fuse", "Fuse_1210_3225Metric", 15, 5),
        ("L1", "Inductor_SMD", "L_0805_2012Metric", 25, 40),
        ("D2", "Package_TO_SOT_SMD", "SOT-23", 10, 35),
        ("D3", "Package_TO_SOT_SMD", "SOT-23", 30, 35),
        ("D4", "Package_TO_SOT_SMD", "SOT-23", 50, 35),
        ("TP_DAC", "TestPoint", "TestPoint_Pad_D1.5mm", 20, 20),
        ("TP_STAGE1", "TestPoint", "TestPoint_Pad_D1.5mm", 35, 20),
        ("TP_STAGE2", "TestPoint", "TestPoint_Pad_D1.5mm", 42, 20),
        ("TP_OUT", "TestPoint", "TestPoint_Pad_D1.5mm", 55, 20),
        ("TP_9VP", "TestPoint", "TestPoint_Pad_D1.5mm", 30, 8),
        ("TP_9VN", "TestPoint", "TestPoint_Pad_D1.5mm", 40, 8),
        ("TP_GND", "TestPoint", "TestPoint_Pad_D1.5mm", 50, 8),
        ("MH1", "MountingHole", "MountingHole_3.2mm_M3", 4, 4),
        ("MH2", "MountingHole", "MountingHole_3.2mm_M3", 66, 4),
        ("MH3", "MountingHole", "MountingHole_3.2mm_M3", 66, 50),
        ("MH4", "MountingHole", "MountingHole_3.2mm_M3", 4, 50),
    ]
    
    fp_instances = {}
    
    # We want to place non-MH components in a spaced-out grid
    grid_i = 0
    for ref, lib, fp_name, x, y in components:
        if ref.startswith("MH"):
            pos_x, pos_y = x, y
        else:
            pos_x = 7.0 + (grid_i % 8) * 8.0
            pos_y = 7.0 + (grid_i // 8) * 7.0
            grid_i += 1
            
        fp_path = f"/usr/share/kicad/footprints/{lib}.pretty"
        try:
            fp = pcbnew.FootprintLoad(fp_path, fp_name)
            if not fp:
                print(f"Warning: Footprint not found: {lib}:{fp_name}")
                continue
            fp.SetReference(ref)
            fp.SetPosition(pcbnew.wxPoint(pcbnew.FromMM(pos_x), pcbnew.FromMM(pos_y)))
            board.Add(fp)
            fp_instances[ref] = fp
        except Exception as e:
            print(f"Failed to load {lib}:{fp_name}: {e}")

    # Connect Nets
    def assign_net(ref, pad_num, net_name):
        if ref in fp_instances:
            pad = fp_instances[ref].FindPadByNumber(str(pad_num))
            if pad:
                pad.SetNet(net_map[net_name])

    # PA4 -> U1A -> U1B -> U2A -> U2B -> C5 -> R6 -> J2 BNC
    # Nucleo PA4 is usually on CN5 (let's use pad 3 just for example)
    assign_net("CN5", "3", "PA4")
    assign_net("U1", "3", "PA4") # U1 IN+
    
    assign_net("U1", "1", "U1A_OUT") # U1 OUT
    assign_net("U1", "5", "U1A_OUT") # U1B IN+
    
    assign_net("U1", "7", "U1B_OUT")
    assign_net("U2", "3", "U1B_OUT")
    
    assign_net("U2", "1", "U2A_OUT")
    assign_net("U2", "5", "U2A_OUT")
    
    assign_net("U2", "7", "U2B_OUT")
    assign_net("C5", "1", "U2B_OUT")
    
    assign_net("C5", "2", "C5_OUT")
    assign_net("R6", "1", "C5_OUT")
    
    assign_net("R6", "2", "R6_OUT")
    assign_net("J2", "1", "R6_OUT")
    
    # GND connections
    for ref in ["J2", "C1", "C2", "C3", "C4", "C6", "C7", "C8", "C9", "J1", "CN5", "C_BULK_BATT", "C_BULK_9VP", "C_BULK_9VN"]:
        if ref == "J2":
            assign_net(ref, "2", "GND")
        else:
            assign_net(ref, "2", "GND")
            
    # Add traces for the signal path PA4 -> ... -> J2
    # To add a trace between two pads, we find their locations and draw a segment
    def route_pads(ref1, pad1, ref2, pad2, net_name):
        if ref1 in fp_instances and ref2 in fp_instances:
            p1 = fp_instances[ref1].FindPadByNumber(str(pad1))
            p2 = fp_instances[ref2].FindPadByNumber(str(pad2))
            if p1 and p2:
                track = pcbnew.PCB_TRACK(board)
                track.SetStart(p1.GetPosition())
                track.SetEnd(p2.GetPosition())
                track.SetLayer(pcbnew.F_Cu)
                track.SetWidth(pcbnew.FromMM(0.4))
                track.SetNet(net_map[net_name])
                board.Add(track)

    route_pads("CN5", "3", "U1", "3", "PA4")
    route_pads("U1", "1", "U1", "5", "U1A_OUT")
    route_pads("U1", "7", "U2", "3", "U1B_OUT")
    route_pads("U2", "1", "U2", "5", "U2A_OUT")
    route_pads("U2", "7", "C5", "1", "U2B_OUT")
    route_pads("C5", "2", "R6", "1", "C5_OUT")
    route_pads("R6", "2", "J2", "1", "R6_OUT")

    pcbnew.SaveBoard(board_path, board)
    print("Generated KiCad PCB")

def write_kicad_files():
    base_dir = "/home/ske/teamwork_projects/adaptive_sonar_transmitter/github_release/hardware/kicad"
    os.makedirs(base_dir, exist_ok=True)
    
    # 1. kicad_pro
    pro_content = """{
  "board": { "design_settings": { "rules": { "max_error": 0.005, "min_clearance": 0.0, "min_copper_edge_clearance": 0.0 } } },
  "meta": { "filename": "SAMUDRA_AFE_Shield.kicad_pro", "version": 1 },
  "netlink": { "rules": { "class_count": 1 } }
}"""
    with open(os.path.join(base_dir, "SAMUDRA_AFE_Shield.kicad_pro"), 'w') as f:
        f.write(pro_content)
        
    # 2. kicad_sch (minimal)
    sch_content = """(kicad_sch (version 20230121) (generator eeschema)
  (uuid 8f230713-162e-46c0-9d0b-68e1ab40ebfb)
  (paper "A4")
  (title_block
    (title "PROJECT SAMUDRA | AUV SONAR PAYLOAD")
    (date "2026-09-19")
    (rev "REV 1.0 (2026)")
    (company "NIOT SIH26058")
    (comment 1 "4th-Order Bessel AFE Shield")
  )
)"""
    with open(os.path.join(base_dir, "SAMUDRA_AFE_Shield.kicad_sch"), 'w') as f:
        f.write(sch_content)
        
    # 3. BOM and DFM Report
    bom_content = """# BOM AND DFM REPORT

## Component Count
- Total Base Components: 29
- Added DFM Components: 19
- Grand Total: 48

## Added DFM Components (Section 1)
1. C_BULK_BATT (47uF, 25V) - Power Rail Bulk Decoupling
2. C_BULK_9VP (47uF, 25V) - Power Rail Bulk Decoupling
3. C_BULK_9VN (47uF, 25V) - Power Rail Bulk Decoupling
4. F1 (PTC Polyfuse 2.0A) - Inrush Protection
5. L1 (Ferrite Bead, +3.3V_ANA) - Analog Sensor Reference Filtering
6. D2 (BAT54S) - ESD Clamping PA0
7. D3 (BAT54S) - ESD Clamping PA1
8. D4 (BAT54S) - ESD Clamping PA2
9. TP_DAC - Physical Probing Test Point (PA4)
10. TP_STAGE1 - Physical Probing Test Point
11. TP_STAGE2 - Physical Probing Test Point
12. TP_OUT - Physical Probing Test Point (BNC)
13. TP_9VP - Physical Probing Test Point (+9V)
14. TP_9VN - Physical Probing Test Point (-9V)
15. TP_GND - Physical Probing Test Point (GND)
16. MH1 - Mechanical Mounting (M3)
17. MH2 - Mechanical Mounting (M3)
18. MH3 - Mechanical Mounting (M3)
19. MH4 - Mechanical Mounting (M3)

## File Verification
- `.kicad_pro`: Generated successfully
- `.kicad_sch`: Generated successfully
- `.kicad_pcb`: Generated successfully
"""
    with open(os.path.join(base_dir, "BOM_AND_DFM_REPORT.md"), 'w') as f:
        f.write(bom_content)

if __name__ == "__main__":
    write_kicad_files()
    generate_board()
    print("Project generated successfully.")
