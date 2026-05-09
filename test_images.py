from PIL import Image
import io


def check_img(path):
    try:
        img = Image.open(path)
        print(f"File: {path}, Format: {img.format}, Size: {img.size}")

        img = img.convert('RGBA')
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")

        # Test loading from the buffer
        buffered.seek(0)
        img2 = Image.open(buffered)
        print(f"  Re-loaded Format: {img2.format}")
    except Exception as e:
        print(f"Error on {path}: {e}")


check_img("/home/sino/Scrivania/desk/sinocloud-logo.png")
check_img("/home/sino/Scrivania/sino.jpg")
