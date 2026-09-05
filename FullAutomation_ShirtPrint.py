import base64
import gc
import io
import json
import mimetypes
import os
import re
import shutil
import socket
from urllib import error, request
from PIL import Image
from photoshop import Session
from photoshop.api import ActionDescriptor, AnchorPosition, DialogModes, DocumentFill, ElementPlacement, JPEGSaveOptions, SaveOptions
import struct
import time
from pathlib import Path
import requests

# For Topaz Labs API


# For OpenAI API and image processing
def load_dotenv(dotenv_path=".env"):
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value

def _prepare_image_for_openai(image_path, max_side=1536, jpeg_quality=85):
    with Image.open(image_path) as image_obj:
        prepared = image_obj.convert("RGB")
        prepared.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        output_buffer = io.BytesIO()
        prepared.save(output_buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        image_bytes = output_buffer.getvalue()

    return "image/jpeg", base64.b64encode(image_bytes).decode("utf-8"), len(image_bytes)


def _extract_text_from_response(response_data):
    direct_output_text = response_data.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text.strip()

    output_items = response_data.get("output", [])
    collected = []
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            contents = item.get("content", [])
            if not isinstance(contents, list):
                continue
            for content_item in contents:
                if not isinstance(content_item, dict):
                    continue
                content_type = content_item.get("type")
                if content_type in ("output_text", "text"):
                    text_value = content_item.get("text", "")
                    if isinstance(text_value, str) and text_value.strip():
                        collected.append(text_value.strip())

    return "\n".join(collected).strip()

def create_blank_white_png(output_path, width=10, height=10):
    """
    Create a completely white PNG image.

    Parameters:
        output_path (str): Full output path including filename.
        width (int): Image width in pixels.
        height (int): Image height in pixels.
    """

    output_path = Path(output_path)

    # Create parent folders automatically if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create pure white RGB image
    image = Image.new(
        mode="RGB",
        size=(width, height),
        color=(255, 255, 255)
    )

    # Save as PNG
    image.save(output_path, format="PNG")

    # print(f"Created: {output_path}")


def generate_image_name_description(image_path):
    load_dotenv()

    empty_result = {"title": "", "description": "", "tags": ""}

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set (check .env). Skipping ChatGPT naming/description.")
        return empty_result

    default_model = "gpt-4.1-mini"
    model = os.getenv("OPENAI_MODEL", default_model)
    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"

    try:
        mime_type, image_b64, image_size = _prepare_image_for_openai(image_path)
        print(f"Sending compressed image to ChatGPT ({image_size / 1024:.1f} KB)...")
    except Exception as ex:
        print(f"Failed to prepare image for ChatGPT: {ex}")
        return empty_result

    models_to_try = [model]
    if model != default_model:
        models_to_try.append(default_model)

    response_data = None
    for model_to_use in models_to_try:
        payload = {
            "model": model_to_use,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": """
                            From the attached image, generate Etsy-ready product title, description, tags, materials as described below please:

                            1. Title: make it like this with SEO: "Rubber Duck PNGs – 80s Neon Synthwave T-Shirt Print (Digital Download)"
                            Don't add any subjective adjectives like "Beautiful", "Cute", "Amazing", etc. Focus on descriptive keywords that potential buyers might search for, based on the image content and style. Avoid generic or overly broad keywords. Use a dash "-" to separate the main subject from the style/format. Use parentheses for additional details like "Digital Download" or "Instant Download". Keep it concise and under 140 characters.


                            2. Description: generate it like the following example. Make it short and SEO.
                            Example Description:
                            🍂 **Instant Download — Fall Moose PNG Bundle** 🍂

                            Bring a rugged autumn touch to your next DIY project with this collection of stylish moose designs featuring oversized antlers, sunglasses, rustic clothing, and bold illustrated details.

                            This 5-design bundle is perfect for creators who love woodland animals, fall themes, outdoor-inspired graphics, and distinctive wildlife artwork. Use the PNG files for shirts, sweatshirts, sublimation projects, gifts, stickers, and other creative crafts.

                            3. Tags: generate 13 relevant tags with SEO for this product, separated by commas. Each tag must be less than 20 characters. Focus on descriptive keywords that potential buyers might search for, based on the image content and style. Avoid generic or overly broad tags, and do not include words like "Aesthetic", "Colorful", "Vibrant", etc.
                            
                            Return strict JSON with keys: title, description, tags

                            Note: do NOT include some uncessary words such as "Aesthetic", "Colorful", "Vibrant", etc. 
                            Etsy actually penalizes such words in title and tags, so please avoid them. Focus on descriptive and relevant keywords instead.
                            """
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{image_b64}"
                        }
                    ]
                }
            ]
        }

        req = request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with request.urlopen(req, timeout=240) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as http_error:
            error_body = http_error.read().decode("utf-8", errors="replace")
            try:
                error_json = json.loads(error_body)
                error_code = (error_json.get("error") or {}).get("code")
            except Exception:
                error_code = None

            if error_code == "model_not_found" and model_to_use != default_model:
                print(f"Model '{model_to_use}' not found. Retrying with '{default_model}'...")
                continue

            print(f"ChatGPT API HTTP error: {http_error.code} - {error_body}")
            return empty_result
        except (TimeoutError, socket.timeout):
            print("ChatGPT API request timed out. Try again or use a smaller source image.")
            return empty_result
        except Exception as ex:
            print(f"ChatGPT API request failed: {ex}")
            return empty_result

    if response_data is None:
        print("No response received from ChatGPT.")
        return empty_result

    raw_text = _extract_text_from_response(response_data)
    if not raw_text:
        print("No text output returned from ChatGPT.")
        print(f"Raw response keys: {list(response_data.keys())}")
        return empty_result

    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r"^```[a-zA-Z]*\n?", "", json_text)
        json_text = re.sub(r"```$", "", json_text).strip()

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        print("Failed to parse JSON response from ChatGPT.")
        print(f"Raw response text: {raw_text}")
        return empty_result

    title = (parsed.get("title") or "").strip()
    description = (parsed.get("description") or "").strip()
    tags = parsed.get("tags") or ""
    tags = ", ".join(str(tag).strip() for tag in tags) if isinstance(tags, list) else str(tags).strip()

    print("\n=== ChatGPT Result ===")
    print(f"Title: {title if title else '[not parsed]'}\n")
    print(f"Description: {description if description else '[not parsed]'}\n")
    print(f"Tags: {tags if tags else '[not parsed]'}\n\n")

    return {"title": title, "description": description, "tags": tags}

def image_file_organizing(image_path, name_data):
    title = (name_data or {}).get("title", "")
    if not title:
        print("No title available; skipping file organizing.")
        return image_path

    name1 = title.strip()
    for delimiter in (" \u2013 ", " \u2014 ", " - "):
        if delimiter in title:
            name1 = title.split(delimiter)[0].strip()
            break
    name2 = name1.replace(" ", "")

    path_obj = Path(image_path)
    new_stem = name2
    new_path = path_obj.with_name(f"{new_stem}{path_obj.suffix}")

    if new_path != path_obj:
        gc.collect()  # release any lingering PIL file handles before renaming
        last_error = None
        for attempt in range(5):
            try:
                path_obj.rename(new_path)
                shutil.copy2(str(new_path), "RawImage.png")
                break
            except PermissionError as ex:
                last_error = ex
                time.sleep(1)
        else:
            raise last_error
        print(f"Renamed image file to: {new_path}")

    product_root = Path(r"D:\KnightlyStorage\EtsyHustle\DigitalShirtPrint\Product")
    existing_numbers = []
    for entry in product_root.iterdir():
        if entry.is_dir():
            match = re.match(r"^(\d+)_", entry.name)
            # print(f"\nChecking existing folder: {entry.name}, match: {match}")
            if match:
                existing_numbers.append(int(match.group(1)))
    next_number = max(existing_numbers, default=0) + 1
    new_folder = product_root / f"{next_number}_{name2}"
    new_folder.mkdir(parents=True, exist_ok=True)
    print(f"Created product folder: {new_folder}")

    final_path = new_folder / new_path.name
    shutil.copy2(str(new_path), str(final_path))

    # Generate blank white PNGs for mockups and samples
    for i in range(1, 6):
        create_blank_white_png(str(final_path).replace(".png", f"_{i}.png"), width=10, height=10)
        create_blank_white_png(new_folder / f"Mockup{i}.png", width=10, height=10)
    create_blank_white_png(new_folder / f"Samples.png", width=10, height=10)

    print(f"Moved image file to: {final_path}")

    return str(final_path)

def set_image_dpi(image_path, dpi=300):
    """Stamp print DPI metadata onto the image (no pixel upscaling)."""
    with Image.open(image_path) as img:
        img.save(image_path, dpi=(dpi, dpi))
    print(f"Set DPI to {dpi} for: {image_path}")

def psd_setup(image_path):
    with Session(image_path, action="open") as ps:
        doc = ps.active_document
        width, height = doc.width, doc.height
        if width >= height:
            new_width, new_height = 2000, round(2000 * height / width)
        else:
            new_width, new_height = round(2000 * width / height), 2000
        doc.resizeImage(new_width, new_height)
        doc.app.resizeCanvas(2000, 2000, AnchorPosition.MiddleCenter)
        # doc.flatten()

def samples_image_automation(image_path):
    path_obj = Path(image_path)
    stem = path_obj.stem
    parent = path_obj.parent

    with Session() as ps:
        target_doc = ps.app.documents.add(2000, 2000, name="Samples", initialFill=DocumentFill.White)

        for i in range(1, 6):
            sample_path = parent / f"{stem}_{i}.png"
            with Session(str(sample_path), action="open", auto_close=True) as src_ps:
                src_doc = src_ps.active_document
                src_doc.activeLayer.duplicate(target_doc.app, ElementPlacement.PlaceAtBeginning)

        ps.app.activeDocument = target_doc.app


# ========================== Automation V1 =========================

imageFile = r"RawImage.png"
nameData = generate_image_name_description(imageFile)
productFilePath = image_file_organizing(imageFile, nameData)
set_image_dpi(productFilePath)
psd_setup(productFilePath)

# Manual Step Gap

# samples_image_automation(productFilePath)


# create_all_dark_mockups(imageFile)