import zipfile
import os

def package_project():
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    zip_filename = os.path.join(ROOT, "captop_deploy.zip")
    include_files = [os.path.join(ROOT, item) for item in [
        "labeler/app.py",
        "labeler/static",
        "labeler/templates",
        "data/captchas"
    ]]
    
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
        for item in include_files:
            if os.path.isfile(item):
                zip_ref.write(item)
            elif os.path.isdir(item):
                for root, dirs, files in os.walk(item):
                    for file in files:
                        zip_ref.write(os.path.join(root, file))
    
    print(f"Successfully created {zip_filename}")
    print("Upload this file to PythonAnywhere and run 'unzip captop_deploy.zip' in your console.")

if __name__ == "__main__":
    package_project()
