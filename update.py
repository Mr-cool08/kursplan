import os
import subprocess
import sys


def in_venv():
    return sys.prefix != sys.base_prefix

def update():
    subprocess.run(["git", "pull"], check=True) #git pull
    if not os.path.exists("venv"):
        subprocess.run(["python3", "-m", "venv", "venv"], check=True) #python3 -m venv venv
    if not in_venv():
        subprocess.run(["bash", "-c", "source venv/bin/activate && pip install --upgrade pip"], check=True) #activate venv # source venv/bin/activate
    subprocess.run(["pip", "install", "-r", "requirements.txt"], check=True) # pip install -r requirements.txt
    subprocess.run(["pytest", "-n", "auto", "--maxfailures=1"], check=True) # pytest -n auto --maxfailures=1
    subprocess.run(["docker", "compose", "pull"], check=True) # docker compose pull
    subprocess.run(["docker", "compose", "down"], check=True) # docker compose down
    subprocess.run(["docker", "compose", "rm", "-f"], check=True) # docker compose rm -f
    subprocess.run(["docker", "compose", "up", "-d", "--build", "--force-recreate", "--remove-orphans"], check=True) # docker compose up -d --build --force-recreate --remove-orphans
    
    
if __name__ == "__main__":
    update()