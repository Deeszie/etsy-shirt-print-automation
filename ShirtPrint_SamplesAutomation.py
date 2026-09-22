import shutil
from pathlib import Path
from photoshop import Session
from photoshop.api import ActionDescriptor, DialogModes, SaveOptions, JPEGSaveOptions, ElementPlacement
from photoshop.api._layerSet import LayerSet
import re

TEMPLATE_PSD_PATH = r"D:\KnightlyStorage\EtsyHustle\DigitalShirtPrint\Archive\ProductTemplate.psd"
TITLE_LAYER_NAME = "PNG_TITLE"


def find_layer_by_name(layers, target_name):
    """Recursively search a layer/layer-set collection for a layer with the given name."""
    for layer in layers:
        if layer.name == target_name:
            return layer
        if isinstance(layer, LayerSet):
            found = find_layer_by_name(layer.layers, target_name)
            if found:
                return found
    return None


def replace_placeholder_image(app, parent_doc, target_layer_name, image_path):
    """Replace a smart-object placeholder layer's contents with the given image."""
    placeholder_layer = find_layer_by_name(parent_doc.layers, target_layer_name)
    if placeholder_layer is None:
        raise RuntimeError(f'Could not find layer "{target_layer_name}" in {parent_doc.fullName}')

    parent_doc.activeLayer = placeholder_layer
    app.executeAction(
        app.stringIDToTypeID("placedLayerEditContents"),
        ActionDescriptor(),
        DialogModes.DisplayNoDialogs,
    )

    smart_doc = app.activeDocument
    smart_doc_width = float(smart_doc.width)
    smart_doc_height = float(smart_doc.height)

    source_doc = app.open(image_path)
    source_doc.resizeImage(smart_doc_width, smart_doc_height)
    source_doc.selection.select(
        ((0, 0), (smart_doc_width, 0), (smart_doc_width, smart_doc_height), (0, smart_doc_height))
    )
    source_doc.selection.copy()
    source_doc.close(SaveOptions.DoNotSaveChanges)

    app.activeDocument = smart_doc
    smart_doc.paste()
    # The pasted layer may land inside whatever group the old placeholder lived in,
    # so pull it out to the top level first, then wipe everything else (including
    # groups) so no old, non-transparent content is left showing underneath.
    pasted_layer = smart_doc.activeLayer
    pasted_layer.name = "__MOCKUP_ART__"
    pasted_layer.move(smart_doc, ElementPlacement.PlaceAtBeginning)
    for layer in [smart_doc.layers[i] for i in range(smart_doc.layers.length)]:
        if layer.name != "__MOCKUP_ART__":
            layer.remove()
    smart_doc.save()
    smart_doc.close()

    app.activeDocument = parent_doc


def create_samples_psd(input_path):
    input_folder = Path(input_path)
    copied_psd_path = input_folder / Path(TEMPLATE_PSD_PATH).name

    # 1. Copy the PSD template into the input path
    shutil.copy2(TEMPLATE_PSD_PATH, copied_psd_path)

    # 2. Open the copied PSD file
    with Session(str(copied_psd_path), action="open") as ps:
        doc = ps.active_document
        app = ps.app

        # 3. Change "PNG_TITLE" text layer contents to the input folder's name
        title_layer = find_layer_by_name(doc.layers, TITLE_LAYER_NAME)
        if title_layer is None:
            raise RuntimeError(f'Could not find layer "{TITLE_LAYER_NAME}" in {copied_psd_path}')
        
        text = input_folder.name.split("_")[1] if "_" in input_folder.name else input_folder.name
        # Finds a lowercase letter followed by an uppercase letter, then inserts a space between them
        result = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
        title_layer.textItem.contents = result

        # 4. Replace PLACEHOLDER1..PLACEHOLDER5 smart objects with <text>_1.png..<text>_5.png
        for i in range(1, 6):
            image_path = input_folder / f"{text}_{i}.png"
            replace_placeholder_image(app, doc, f"PLACEHOLDER{i}", str(image_path))

        doc.save()

    print(f"Updated '{TITLE_LAYER_NAME}' to '{input_folder.name}' in: {copied_psd_path}")
    return str(copied_psd_path)


def create_mockup(psd_path, image_path, output_path):
    target_layer_name = "ARTWORK_PLACEHOLDER"

    with Session(psd_path, action="open") as ps:
        app = ps.app
        parent_doc = ps.active_document

        matching_layers = [
            parent_doc.artLayers[i]
            for i in range(parent_doc.artLayers.length)
            if parent_doc.artLayers[i].name == target_layer_name
        ]

        if not matching_layers:
            available_layers = [parent_doc.artLayers[i].name for i in range(parent_doc.artLayers.length)]
            raise RuntimeError(
                f'Could not find layer "{target_layer_name}" in {psd_path}. '
                f"Available top-level layers: {available_layers}"
            )

        placeholder_layer = matching_layers[0]
        parent_doc.activeLayer = placeholder_layer
        app.executeAction(
            app.stringIDToTypeID("placedLayerEditContents"),
            ActionDescriptor(),
            DialogModes.DisplayNoDialogs,
        )

        smart_doc = app.activeDocument
        smart_doc_width = float(smart_doc.width)
        smart_doc_height = float(smart_doc.height)

        source_doc = app.open(image_path)
        source_doc.resizeImage(smart_doc_width, smart_doc_height)
        source_doc.selection.select(
            ((0, 0), (smart_doc_width, 0), (smart_doc_width, smart_doc_height), (0, smart_doc_height))
        )
        source_doc.selection.copy()
        source_doc.close(SaveOptions.DoNotSaveChanges)

        app.activeDocument = smart_doc
        smart_doc.paste()
        smart_doc.flatten()
        smart_doc.save()
        smart_doc.close()

        app.activeDocument = parent_doc

        jpeg_options = JPEGSaveOptions()
        jpeg_options.quality = 12
        parent_doc.saveAs(output_path, jpeg_options, True)

def path_from_stored():
    """Retrieve the stored final path of the processed image."""
    try:
        with open("storing_path.txt", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    
if __name__ == "__main__":
    stored_path = path_from_stored()
    if stored_path:
        create_samples_psd(stored_path)
    else:
        print("No stored path found.")