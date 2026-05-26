"""DummyAuthenticator — dcucode_sso 통합 버전.

클래스 이름은 호환 위해 보존(`DummyAuthenticator`). 내부 구현만 SSO 호출로 갈음.
운영자는 config 의 `authenticator_class` 변경 없이 자동으로 SSO 인증 사용.

흐름 2가지:
  1) SSH client (일반):  username + password
        → SSO `/oauth/token`  grant_type=password (ROPC)
  2) OJ frontend Container.vue (webssh):  username='dcucode-<real>'  password=<SSO access_token>
        → SSO `/userinfo`  Bearer <token> → preferred_username 매칭 검증

옛 OJ backend `/api/login` · `/api/token_auth` 호출 흐름은 제거. SSO 가 인증 권위.
"""
import os

from kubessh.authentication import Authenticator
import requests
from traitlets import Unicode, Float


class DummyAuthenticator(Authenticator):
    """환경변수 우선, config 도 가능 (config 가 더 강함).

    K8s/docker 의 env 만으로 운영 가능. config 파일 변경 불필요.
    """

    sso_token_url = Unicode(
        os.environ.get("SSO_TOKEN_URL", "http://dcu-sso:8000/oauth/token"),
        config=True,
        help="SSO OAuth token endpoint. env: SSO_TOKEN_URL",
    )
    sso_userinfo_url = Unicode(
        os.environ.get("SSO_USERINFO_URL", "http://dcu-sso:8000/userinfo"),
        config=True,
        help="SSO OIDC userinfo endpoint. env: SSO_USERINFO_URL",
    )
    sso_client_id = Unicode(
        os.environ.get("SSO_CLIENT_ID", "kubessh"),
        config=True, help="env: SSO_CLIENT_ID",
    )
    sso_client_secret = Unicode(
        os.environ.get("SSO_CLIENT_SECRET", "dev-kubessh-secret"),
        config=True, help="env: SSO_CLIENT_SECRET (운영은 K8s Secret 권장)",
    )
    sso_scope = Unicode(
        os.environ.get("SSO_SCOPE", "openid profile"),
        config=True, help="env: SSO_SCOPE",
    )
    sso_timeout_sec = Float(
        float(os.environ.get("SSO_HTTP_TIMEOUT", "10")),
        config=True, help="env: SSO_HTTP_TIMEOUT",
    )
    token_username_prefix = Unicode(
        os.environ.get("SSO_TOKEN_USERNAME_PREFIX", "dcucode-"),
        config=True,
        help=(
            "SSH username 이 이 prefix 로 시작하면 password 를 SSO access_token 으로 간주. "
            "OJ Container.vue webssh 호환. env: SSO_TOKEN_USERNAME_PREFIX"
        ),
    )

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        if not username or not password:
            return False
        if self.token_username_prefix and username.startswith(self.token_username_prefix):
            real_username = username[len(self.token_username_prefix):]
            return self._verify_token(real_username, password)
        return self._verify_password(username, password)

    # ----- ROPC (일반 SSH client) -----
    def _verify_password(self, username, password):
        try:
            r = requests.post(
                self.sso_token_url,
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                    "scope": self.sso_scope,
                    "client_id": self.sso_client_id,
                    "client_secret": self.sso_client_secret,
                },
                timeout=self.sso_timeout_sec,
            )
        except requests.RequestException as e:
            self.log.error(f"SSO ROPC request failed for {username}: {e}")
            return False
        if r.status_code != 200:
            self.log.info(f"SSO ROPC denied for {username}: status={r.status_code} body={r.text[:200]}")
            return False
        if not (r.json() if r.content else {}).get("access_token"):
            self.log.info(f"SSO ROPC: no access_token for {username}")
            return False
        self.log.info(f"SSO ROPC ok for {username}")
        return True

    # ----- Bearer token (Container.vue webssh) -----
    def _verify_token(self, expected_username, token):
        try:
            r = requests.get(
                self.sso_userinfo_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.sso_timeout_sec,
            )
        except requests.RequestException as e:
            self.log.error(f"SSO userinfo failed for {expected_username}: {e}")
            return False
        if r.status_code != 200:
            self.log.info(f"SSO token denied for {expected_username}: status={r.status_code} body={r.text[:200]}")
            return False
        data = r.json() if r.content else {}
        actual = data.get("preferred_username", "")
        if actual != expected_username:
            self.log.info(f"SSO token user mismatch: token={actual} username={expected_username}")
            return False
        self.log.info(f"SSO token ok for {expected_username}")
        return True
