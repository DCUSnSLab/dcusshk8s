from kubessh.authentication import Authenticator
import requests
import json
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64


class DummyAuthenticator(Authenticator):
    """
    Dummy SSH Authenticator.

    Allows ssh logins where the username is the same as the password.
    """
    def password_auth_supported(self):
        return True

    def get_public_key(self):
        try:
            #response = requests.get('http://203.250.33.87:31320/api/get_public_key')
            response = requests.get('http://203.250.33.85/api/get_public_key')
            if response.status_code == 200:
                return response.json()['data']['public_key']
            else:
                self.log.error(f"Failed to get public key: {response.status_code}")
                return None
        except Exception as e:
            self.log.error(f"Error fetching public key: {str(e)}")
            return None

    def encrypt_password(self, public_key_str, password):
        try:
            public_key = RSA.import_key(public_key_str)
            cipher = PKCS1_v1_5.new(public_key)
            encrypted_password = cipher.encrypt(password.encode())
            return base64.b64encode(encrypted_password).decode('utf-8')
        except Exception as e:
            self.log.error(f"Error encrypting password: {str(e)}")
            return None

    def validate_password(self, username, password):
        self.log.info(f"Login attempted by {username}")
        
        public_key = self.get_public_key()
        if not public_key:
            return False
        
        encrypted_password = self.encrypt_password(public_key, password)
        if not encrypted_password:
            return False

        if username.split('-')[0] == 'dcucode':
            # tokenLoginUrl = 'http://203.250.33.85/api/token_auth'
            tokenLoginUrl = 'http://203.250.33.87:30481/api/token_auth' # dcucode dev
            data = {
                'token': password
            }
            response = requests.post(tokenLoginUrl, json=data)
            if response.status_code == 200:
                response_data = json.loads(response.text)

                self.log.info(response_data['data'])
    
                if response_data['error'] == None:
                    return True
            return False
        else:
            #url = 'http://203.250.33.87:30481/api/login' # dcucode dev
            url = 'http://203.250.33.85/api/login'
            data = {
                'username': username,
                'password': encrypted_password
            }
            response = requests.post(url, json=data)
            print("HTTP response status code :", response.status_code)

            if response.status_code == 200:
                response_data = json.loads(response.text)

                self.log.info(response_data['data'])
    
                if response_data['error'] == None:
                    return True
            #print("Response text:\n", response.text)
            return False
        
        

        

