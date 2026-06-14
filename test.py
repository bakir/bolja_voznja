import fitz  # PyMuPDF

def inspect_drawings(pdf_path, page_num=0, path_limit=100):
    # Open the document and select a page
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    # Extract the vector graphics
    drawings = page.get_drawings()
    
    print(f"Found {len(drawings)} drawing paths on Page {page_num + 1}\n")
    print("-" * 50)
    
    # Loop through the drawings (limited so it doesn't flood your console)
    for i, path in enumerate(drawings[:path_limit]):
        print(f"Path [{i}]:")
        
        # 1. Look at the styling attributes
        print(f"  Stroke Color (Outline): {path.get('color')}")
        print(f"  Fill Color (Inside):    {path.get('fill')}")
        print(f"  Line Width:             {path.get('width')}")
        
        # 2. Look at the actual geometric shapes in this path
        print("  Items (Geometry):")
        for item in path["items"]:
            command = item[0]
            # if command == "re":
            #     continue
            
            if command == "l":
                # Line segment: ('l', Point(x1, y1), Point(x2, y2))
                print(f"    -> Line from (x:{item[1].x:.1f}, y:{item[1].y:.1f}) to (x:{item[2].x:.1f}, y:{item[2].y:.1f})")
                
            elif command == "re":
                # Rectangle: ('re', Rect(x0, y0, x1, y1))
                rect = item[1]
                print(f"    -> Rectangle [Top-Left: ({rect.x0:.1f}, {rect.y0:.1f}), Bottom-Right: ({rect.x1:.1f}, {rect.y1:.1f})]")
                
            elif command == "c":
                # Cubic Bezier Curve (used for rounded corners or circles)
                print(f"    -> Curve ending at (x:{item[4].x:.1f}, y:{item[4].y:.1f})")
                
            else:
                print(f"    -> {item}")
        print("-" * 50)

if __name__ == "__main__":
    # Replace with your actual file name
    pdf_file = "katalog1.pdf" 
    inspect_drawings(pdf_file, page_num=0, path_limit=100)