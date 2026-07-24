import os
from PIL import Image, ImageDraw, ImageFont

def draw_lm_logo(size):
    # Create image with transparent background
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Calculate padding and border radius based on size
    pad = max(1, int(size * 0.05))
    radius = max(2, int(size * 0.22))
    
    rect = [pad, pad, size - pad, size - pad]
    
    # Draw base rounded rectangle badge (#2563EB)
    bg_color = (37, 99, 235, 255)
    border_color = (96, 165, 250, 255)
    
    # Outer subtle border line
    draw.rounded_rectangle(rect, radius=radius, fill=bg_color, outline=border_color, width=max(1, int(size*0.03)))
    
    # Font setup
    font_size = int(size * 0.45)
    
    # Try loading Arial or Segoe UI bold font, fallback to default font
    font = None
    for font_name in ["arialbd.ttf", "segoeuib.ttf", "Segoe UI Bold.ttf", "arial.ttf"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except Exception:
            continue
            
    if font is None:
        font = ImageFont.load_default()

    text = "LM"
    
    # Measure text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    
    # Centering coordinates
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]
    
    # Draw sharp white text
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    
    return img

def main():
    sizes = [16, 32, 48, 64, 128, 256]
    # For PIL ICO format, pass largest image and list of sizes tuple
    largest_img = draw_lm_logo(256)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(script_dir, "logo.ico")
    
    # Save multi-resolution ICO file in PIL:
    largest_img.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes]
    )
    print(f"Generated multi-res icon successfully at: {ico_path}")

if __name__ == "__main__":
    main()
