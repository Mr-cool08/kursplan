import os
import subprocess
import sys
from dotenv import load_dotenv


load_dotenv()
def is_port_available(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0
def before_update():
    #Check if ENV exists
    if not os.path.exists(".env"):
        print("Error: .env file not found. Please create a .env file before updating.")
        sys.exit(1)
    #Check if requirements.txt exists
    if not os.path.exists("requirements.txt"):
        print("Error: requirements.txt file not found. Please create a requirements.txt file before updating.")
        sys.exit(1)
    if not os.path.exists("docker-compose.yml"):
        print("Error: docker-compose.yml file not found. Please create a docker-compose.yml file before updating.")
        sys.exit(1)
    if not os.path.exists("app.py"):
        print("Error: app.py file not found. Please create a app.py file before updating.")
        sys.exit(1)
    #check if port is available
    if not is_port_available(load_dotenv().get("app_port", 80)):
        print("Error: Port " + str(load_dotenv().get("app_port", 80)) + " is not available. Please free the port before updating.")
        sys.exit(1)
def update():
    subprocess.run(["git", "pull"], check=True) #git pull
    if not os.path.exists("venv"):
        subprocess.run(["python3", "-m", "venv", "venv"], check=True) #python3 -m venv venv
    python = "venv/bin/python"
    subprocess.run([python, "-m", "ensurepip", "--upgrade"], check=True) # python -m ensurepip install --upgrade
    subprocess.run([python, "-m", "pip", "install", "--upgrade", "pip"], check=True) # python -m pip install --upgrade pip
    subprocess.run([python, "-m", "pip", "install", "-r", "requirements.txt"], check=True) # python -m pip install -r requirements.txt
    subprocess.run([python, "-m", "pytest", "-n", "auto", "--maxfail=1"], check=True) # python -m pytest -n auto --maxfail=1
    subprocess.run(["docker", "compose", "pull"], check=True) # docker compose pull
    subprocess.run(["docker", "compose", "down"], check=True) # docker compose down
    subprocess.run(["docker", "compose", "rm", "-f"], check=True) # docker compose rm -f
    subprocess.run(["docker", "compose", "up", "-d", "--build", "--force-recreate", "--remove-orphans"], check=True) # docker compose up -d --build --force-recreate --remove-orphans
    subprocess.run(["ping", "google.com", "-c", "10"], check=True) # ping google.com -c 100    
    subprocess.run(["ping", "kursplan.se", "-c", "10"], check=True) # ping kursplan.se -c 100
    
    
if __name__ == "__main__":
    before_update()
    update()