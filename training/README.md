# Scripts

Utility scripts for various project tasks.

- **init_db.py**: Initializes the SQLite database and populates images.
- **relabel.py**: Uses AI models (Ollama) to perform automated relabeling. (This was too slow so I crowdsourced it; find the implementation in the **server** directory or visit https://sykik.pythonanywhere.com or https://captop.sykik.workers.dev.)
- **deeplearn.py**: PyTorch script for training the captcha recognition model.
- **package.py**: Packages the project into a zip file for deployment.
