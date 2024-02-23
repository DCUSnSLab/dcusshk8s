from kubessh.authentication import Authenticator
import requests
import json

class DummyAuthenticator(Authenticator):
    """
    Dummy SSH Authenticator.

    Allows ssh logins where the username is the same as the password.
    """
    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        self.log.info(f"Login attempted by {username}")

        url = 'http://203.250.33.85/api/login'
#        url = 'http://203.250.33.99/api/login'
        data = {
            'username': username,
            'password': password
        }
        
        response = requests.post(url, data=data)
        print("HTTP response status code :", response.status_code)

        if response.status_code == 200:
            response_data = json.loads(response.text)

            self.log.info(response_data['data'])

            if response_data['error'] == None:
                return True

        return False
