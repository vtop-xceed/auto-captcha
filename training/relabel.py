# use ollama model locally to identify captchas and relabel them
import os
import cv2
import shutil
from ollama import generate, Client

# Constants
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "captchas"))
MODEL_NAME = "qwen3-vl:4b"
TEST_COUNT = 10
LABEL_LENGTH = 6
PROMPTS = [
    "Identify the random alphanumeric characters from the given captcha image.",
]

def relabel_images(limit=None):
    client = Client()
    image_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".jpg")]
    
    if limit:
        image_files = image_files[:limit]

    print(f"Relabeling {len(image_files)} images using {MODEL_NAME}...")

    for filename in image_files:
        old_path = os.path.join(DATA_DIR, filename)
        
        # Preprocess image to help OCR (optional but recommended for noise)
        temp_img_path = os.path.join(DATA_DIR, f"temp_{filename}")
        img = cv2.imread(old_path)
        if img is not None:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # Simple threshold to remove noise if possible
            _, binary = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            cv2.imwrite(temp_img_path, binary)
            ocr_path = temp_img_path
        else:
            ocr_path = old_path

        found_label = None
        try:
            for base_prompt in PROMPTS:
                prompt_str = f"{ocr_path}\n{base_prompt}"
                response = client.generate(
                    model=MODEL_NAME,
                    prompt=prompt_str,
                    images=[ocr_path]
                )
                
                label = response['response'].strip().upper()
                # Clean label: remove punctuation, spaces, and non-alphanumeric
                label = "".join([c for c in label if c.isalnum()])
                
                # If we get something close to expected length, we take it
                if len(label) == LABEL_LENGTH:
                    found_label = label
                    break
            
            if found_label:
                new_filename = f"{found_label}.jpg"
                new_path = os.path.join(DATA_DIR, new_filename)
                
                if old_path != new_path:
                    shutil.move(old_path, new_path)
                    print(f"Renamed: {filename} -> {new_filename}")
                else:
                    print(f"Skipped (already correct): {filename}")
            else:
                print(f"Warning: {MODEL_NAME} failed to find {LABEL_LENGTH}-char label for {filename}")

        except Exception as e:
            print(f"Error processing {filename}: {e}")
        finally:
            if os.path.exists(temp_img_path):
                os.remove(temp_img_path)

if __name__ == "__main__":
    # First 10 for verification
    relabel_images(limit=TEST_COUNT)
    print("\nTest run complete. Please verify the filenames in the 'captchas' folder.")
