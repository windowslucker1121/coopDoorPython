
import ruamel.yaml as YAML

class SecretsReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def read_secrets(self):
        with open(self.file_path, 'r') as file:
            yaml = YAML.YAML()
            yamlcontent = yaml.load(file)
            return yamlcontent['secrets']