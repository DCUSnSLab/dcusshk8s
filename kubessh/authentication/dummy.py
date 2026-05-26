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
            tokenLoginUrl = 'http://203.250.33.85/api/token_auth'
            # tokenLoginUrl = 'http://203.250.33.87:30481/api/token_auth' # dcucode dev
            # tokenLoginUrl = 'http://203.250.33.87:31617/api/token_auth' # dcucode test
            real_username = username.split('-', 1)[1]
            data = {
                'token': password,
                'username': real_username
            }
            response = requests.post(tokenLoginUrl, json=data)
            if response.status_code == 200:
                response_data = json.loads(response.text)

                self.log.debug(response_data['data'])

                if response_data['error'] is None:
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
            self.log.info(f"HTTP response status code : {response.status_code}")

            if response.status_code == 200:
                response_data = json.loads(response.text)

                self.log.debug(response_data['data'])

                if response_data['error'] is None:
                    return True
            #print("Response text:\n", response.text)
            return False


# ====================================================================
# [SSO 통합 버전 — 보존용 주석. 검증 후 활성화 결정]
# ====================================================================
# 흐름:
#   1) SSH client (일반):  username + password → SSO /oauth/token (ROPC)
#   2) Container.vue webssh: username='dcucode-<real>' password=<access_token>
#      → SSO /userinfo → preferred_username 매칭 검증
#
# 환경변수:
#   SSO_TOKEN_URL              http://dcu-sso:8000/oauth/token
#   SSO_USERINFO_URL           http://dcu-sso:8000/userinfo
#   SSO_CLIENT_ID              kubessh
#   SSO_CLIENT_SECRET          dev-kubessh-secret
#   SSO_SCOPE                  openid profile
#   SSO_HTTP_TIMEOUT           10
#   SSO_TOKEN_USERNAME_PREFIX  dcucode-
# --------------------------------------------------------------------
# import os
# from traitlets import Unicode, Float
#
# class DummyAuthenticator(Authenticator):
#     sso_token_url = Unicode(
#         os.environ.get("SSO_TOKEN_URL", "http://dcu-sso:8000/oauth/token"),
#         config=True, help="env: SSO_TOKEN_URL",
#     )
#     sso_userinfo_url = Unicode(
#         os.environ.get("SSO_USERINFO_URL", "http://dcu-sso:8000/userinfo"),
#         config=True, help="env: SSO_USERINFO_URL",
#     )
#     sso_client_id = Unicode(os.environ.get("SSO_CLIENT_ID", "kubessh"), config=True)
#     sso_client_secret = Unicode(
#         os.environ.get("SSO_CLIENT_SECRET", "dev-kubessh-secret"), config=True,
#     )
#     sso_scope = Unicode(os.environ.get("SSO_SCOPE", "openid profile"), config=True)
#     sso_timeout_sec = Float(
#         float(os.environ.get("SSO_HTTP_TIMEOUT", "10")), config=True,
#     )
#     token_username_prefix = Unicode(
#         os.environ.get("SSO_TOKEN_USERNAME_PREFIX", "dcucode-"), config=True,
#     )
#
#     def password_auth_supported(self):
#         return True
#
#     def validate_password(self, username, password):
#         if not username or not password:
#             return False
#         if self.token_username_prefix and username.startswith(self.token_username_prefix):
#             real_username = username[len(self.token_username_prefix):]
#             return self._verify_token(real_username, password)
#         return self._verify_password(username, password)
#
#     def _verify_password(self, username, password):
#         try:
#             r = requests.post(
#                 self.sso_token_url,
#                 data={
#                     "grant_type": "password",
#                     "username": username,
#                     "password": password,
#                     "scope": self.sso_scope,
#                     "client_id": self.sso_client_id,
#                     "client_secret": self.sso_client_secret,
#                 },
#                 timeout=self.sso_timeout_sec,
#             )
#         except requests.RequestException as e:
#             self.log.error(f"SSO ROPC request failed for {username}: {e}")
#             return False
#         if r.status_code != 200:
#             self.log.info(
#                 f"SSO ROPC denied for {username}: status={r.status_code} body={r.text[:200]}"
#             )
#             return False
#         if not (r.json() if r.content else {}).get("access_token"):
#             self.log.info(f"SSO ROPC: no access_token for {username}")
#             return False
#         self.log.info(f"SSO ROPC ok for {username}")
#         return True
#
#     def _verify_token(self, expected_username, token):
#         try:
#             r = requests.get(
#                 self.sso_userinfo_url,
#                 headers={"Authorization": f"Bearer {token}"},
#                 timeout=self.sso_timeout_sec,
#             )
#         except requests.RequestException as e:
#             self.log.error(f"SSO userinfo failed for {expected_username}: {e}")
#             return False
#         if r.status_code != 200:
#             self.log.info(
#                 f"SSO token denied for {expected_username}: status={r.status_code} body={r.text[:200]}"
#             )
#             return False
#         data = r.json() if r.content else {}
#         actual = data.get("preferred_username", "")
#         if actual != expected_username:
#             self.log.info(
#                 f"SSO token user mismatch: token={actual} username={expected_username}"
#             )
#             return False
#         self.log.info(f"SSO token ok for {expected_username}")
#         return True
