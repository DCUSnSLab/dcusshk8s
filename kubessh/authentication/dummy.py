from kubessh.authentication import Authenticator
import requests
import json
import geoip2.database
from datetime import datetime


class DummyAuthenticator(Authenticator):
    """
    Dummy SSH Authenticator.

    Allows ssh logins where the username is the same as the password.
    """
    def password_auth_supported(self):
        return True


    def validate_password(self, username, password):
        print("--------------------------------------------------------------")
        print("validate_password()")
        # get user info
        self.log.info(f"Login attempted by {username}")

        client_ip = self.conn.get_extra_info('peername')[0]
        access_time = datetime.now()

        database_path = './kubessh/GeoLite2-City.mmdb'
        try:
            with geoip2.database.Reader(database_path) as reader:
                response = reader.city(client_ip)
                access_country = response.country.iso_code
                access_city = response.city.name
        except Exception as e:
            print(f"Error: {e}")
            access_country = None
            access_city = None

        print(f"user ID : {username}")
        print(f"user IP : {client_ip}")
        print(f"access time : {access_time}")
        print(f"country : {access_country}")
        print(f"city : {access_city}")


        # login in using DCU code API
        url = 'http://203.250.33.85/api/login'
        data = {
            'username': username,
            'password': password
        }
        
        response = requests.post(url, data=data)
        print("HTTP response status code :", response.status_code)

        print("--------------------------------------------------------------")
        if response.status_code == 200:
            response_data = json.loads(response.text)

            self.log.info(response_data['data'])

            if response_data['error'] == None:
                return True

        return False

    def connection_made(self, conn):
        self.conn = conn

        print("--------------------------------------------------------------")
        print("connection_made()")
        # get user info
        username = self.conn.get_extra_info('username')

        client_ip = self.conn.get_extra_info('peername')[0]
        access_time = datetime.now()

        database_path = './kubessh/GeoLite2-City.mmdb'
        try:
            with geoip2.database.Reader(database_path) as reader:
                response = reader.city(client_ip)
                access_country = response.country.iso_code
                access_city = response.city.name
        except Exception as e:
            print(f"Error: {e}")
            access_country = None
            access_city = None

        print(f"user ID : {username}")
        print(f"user IP : {client_ip}")
        print(f"access time : {access_time}")
        print(f"country : {access_country}")
        print(f"city : {access_city}")

        print("--------------------------------------------------------------")
