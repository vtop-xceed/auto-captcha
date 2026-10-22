#!/usr/bin/env python3
import os
import random
import zipfile
import shutil

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'captchas')
ZIP_200 = os.path.join(DATA_DIR, 'dataset_200.zip')
ZIP_500 = os.path.join(DATA_DIR, 'dataset_500.zip')
ZIP_811 = os.path.join(DATA_DIR, 'dataset_811.zip')

def create_zips():
    # Get all image files
    images = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    images.sort() # For deterministic behavior if needed, or random
    
    total_count = len(images)
    print(f"Found {total_count} images.")
    
    # 811 zip (all)
    print(f"Creating {ZIP_811}...")
    with zipfile.ZipFile(ZIP_811, 'w') as z:
        for img in images:
            z.write(os.path.join(DATA_DIR, img), img)
            
    # 500 zip (random)
    print(f"Creating {ZIP_500}...")
    images_500 = random.sample(images, min(500, total_count))
    with zipfile.ZipFile(ZIP_500, 'w') as z:
        for img in images_500:
            z.write(os.path.join(DATA_DIR, img), img)
            
    # 200 zip (random)
    print(f"Creating {ZIP_200}...")
    images_200 = random.sample(images, min(200, total_count))
    with zipfile.ZipFile(ZIP_200, 'w') as z:
        for img in images_200:
            z.write(os.path.join(DATA_DIR, img), img)

def cleanup_images():
    print("Removing individual images...")
    images = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    for img in images:
        os.remove(os.path.join(DATA_DIR, img))
    print("Cleanup complete.")

if __name__ == "__main__":
    if not os.path.exists(DATA_DIR):
        print(f"Error: {DATA_DIR} does not exist.")
    else:
        create_zips()
        cleanup_images()
