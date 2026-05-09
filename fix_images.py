from PIL import Image


def convert_to_png(src, dest):
    try:
        img = Image.open(src)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        img.save(dest, format="PNG")
        print(f"Success: {dest}")
    except Exception as e:
        print(f"Error converting {src}: {e}")


convert_to_png("/home/sino/Scrivania/desk/sinocloud-logo.png", "/home/sino/Scrivania/desk/sinocloud-logo_real.png")
convert_to_png("/home/sino/Scrivania/sino.jpg", "/home/sino/Scrivania/sino_real.png")
