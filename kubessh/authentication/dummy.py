"""DummyAuthenticator — dcucode_sso 통합 버전.

클래스 이름은 호환 위해 보존(`DummyAuthenticator`). 내부 구현만 SSO 호출로 갈음.
운영자는 config 의 `authenticator_class` 변경 없이 자동으로 SSO 인증 사용.

흐름 2가지:
  1) SSH client (일반):  username + password
        → SSO `/oauth/token`  grant_type=password (ROPC)
  2) OJ frontend Container.vue (webssh):  username='dcucode-<real>'  password=<SSO access_token>
        → SSO `/userinfo`  Bearer <token> → preferred_username 매칭 검증

옛 OJ backend `/api/login` · `/api/token_auth` 호출 흐름은 제거. SSO 가 인증 권위.

환경변수 (default 외 override):
  SSO_TOKEN_URL              http://dcu-sso:8000/oauth/token
  SSO_USERINFO_URL           http://dcu-sso:8000/userinfo
  SSO_CLIENT_ID              kubessh
  SSO_CLIENT_SECRET          dev-kubessh-secret
  SSO_SCOPE                  openid profile
  SSO_HTTP_TIMEOUT           10
  SSO_TOKEN_USERNAME_PREFIX  dcucode-
"""
import os

from kubessh.authentication import Authenticator
import requests
from traitlets import Unicode, Float


class DummyAuthenticator(Authenticator):
    """환경변수 우선, traitlets config 도 가능 (config 가 더 강함).

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
    oj_token_auth_url = Unicode(
        os.environ.get("OJ_TOKEN_AUTH_URL", "http://oj-backend:8000/api/token_auth"),
        config=True,
        help=(
            "OJ backend 의 SimpleJWT 검증 endpoint. Container.vue webssh 가 보내는 "
            "token 은 OJ 자체 SimpleJWT 라 SSO 가 아닌 OJ 가 검증. env: OJ_TOKEN_AUTH_URL"
        ),
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
        # username 이 'dcucode-<real>' 형식이면 → password 가 SSO access_token.
        # OJ frontend 의 Container.vue 가 token 으로 SSH 인증할 때 흐름.
        if self.token_username_prefix and username.startswith(self.token_username_prefix):
            real_username = username[len(self.token_username_prefix):]
            return self._verify_token(real_username, password)
        # 그 외 — username/password 로 ROPC.
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

    # ----- OJ SimpleJWT (Container.vue webssh) -----
    def _verify_token(self, expected_username, token):
        """OJ frontend Container.vue 가 보낸 OJ SimpleJWT 를 OJ backend 로 검증.

        OJ 는 SSO 로그인 후 자체 SimpleJWT(access_token) 를 발급해 localStorage 에 보관.
        그 token 은 OJ 의 SECRET_KEY 로 서명 → SSO 가 모름. OJ 의 /api/token_auth 가
        token 의 user_id + username 매칭을 검증.

        OJ TokenAuthenticationAPI 응답: {"error": null, "data": "Succeeded"}  (성공)
                                        {"error": "...", "data": ...}        (실패)
        """
        try:
            r = requests.post(
                self.oj_token_auth_url,
                json={"token": token, "username": expected_username},
                timeout=self.sso_timeout_sec,
            )
        except requests.RequestException as e:
            self.log.error(f"OJ token_auth failed for {expected_username}: {e}")
            return False
        if r.status_code != 200:
            self.log.info(f"OJ token denied for {expected_username}: status={r.status_code} body={r.text[:200]}")
            return False
        body = r.json() if r.content else {}
        if body.get("error") is not None:
            self.log.info(f"OJ token rejected for {expected_username}: {body.get('error')}")
            return False
        self.log.info(f"OJ token ok for {expected_username}")
        return True
