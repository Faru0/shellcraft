"""curl client certificates with a key password (-E FILE:PASS, --pass, --key, PKCS#12). No network:
requests' Session.send is replaced by a fake that looks at the certificate it was handed."""

import datetime
import os
import stat
import sys

import pytest

from shellcraft.builtins import BUILTINS
from shellcraft.commands import curl as curl_mod
from shellcraft.context import CommandError

crypto = pytest.importorskip("cryptography")
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

PASSWORD = "s3cret:with:colons"


@pytest.fixture
def curl(ctx):
    run = BUILTINS["curl"].fn

    def call(*args):
        return run(ctx, list(args), "")
    return call


@pytest.fixture
def pair():
    """A throwaway self-signed certificate and its key."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shellcraft test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return cert, key


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def key_pem(key, password=None):
    enc = (serialization.BestAvailableEncryption(password.encode()) if password
           else serialization.NoEncryption())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)


class Seen:
    cert = None
    content = b""
    mode = None


@pytest.fixture
def sent(monkeypatch):
    """Replace the transfer: record the cert requests got (and the file behind it), answer 200."""
    import requests

    seen = Seen()

    def fake_send(self, prepared, **kwargs):
        seen.cert = kwargs.get("cert")
        path = seen.cert if isinstance(seen.cert, str) else None
        if path and os.path.exists(path):
            with open(path, "rb") as handle:
                seen.content = handle.read()
            seen.mode = stat.S_IMODE(os.stat(path).st_mode)
        if getattr(seen, "boom", None):
            raise seen.boom
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.headers["Content-Type"] = "text/plain"
        response.raw = __import__("io").BytesIO(b"ok\n")
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)
    return seen


def unlocked_key(seen):
    """The key in the temp file requests was given: it must be unencrypted and loadable."""
    return serialization.load_pem_private_key(seen.content, None)


def same_key(a, b):
    return a.private_numbers() == b.private_numbers()


def test_a_password_after_the_colon_unlocks_a_key_in_the_cert_file(curl, sent, pair, tmp_path):
    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, PASSWORD))
    assert curl("-E", f"{both}:{PASSWORD}", "https://example.test/") == "ok\n"
    assert isinstance(sent.cert, str) and sent.cert != str(both)
    assert same_key(unlocked_key(sent), key)
    assert cert_pem(cert) in sent.content
    assert b"ENCRYPTED" not in sent.content
    if sys.platform != "win32":
        assert sent.mode == 0o600
    assert not os.path.exists(sent.cert)  # removed after the request


def test_pass_with_a_separate_key_file(curl, sent, pair, tmp_path):
    cert, key = pair
    (tmp_path / "c.pem").write_bytes(cert_pem(cert))
    (tmp_path / "c.key").write_bytes(key_pem(key, "pw"))
    curl("--cert", str(tmp_path / "c.pem"), "--key", str(tmp_path / "c.key"), "--pass", "pw", "https://x.test/")
    assert same_key(unlocked_key(sent), key)
    assert not os.path.exists(sent.cert)


def test_an_escaped_colon_is_part_of_the_file_name(curl, sent, pair, tmp_path):
    cert, key = pair
    odd = tmp_path / "a:b.pem"
    odd.write_bytes(cert_pem(cert) + key_pem(key, "pw"))
    escaped = str(odd).replace(":", "\\:")
    curl("-E", f"{escaped}:pw", "https://x.test/")
    assert same_key(unlocked_key(sent), key)


def test_the_temp_file_is_removed_when_the_request_fails(curl, sent, pair, tmp_path):
    import requests

    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, "pw"))
    sent.boom = requests.exceptions.ConnectionError("refused")
    with pytest.raises(CommandError):
        curl("-E", f"{both}:pw", "https://x.test/")
    assert sent.content and not os.path.exists(sent.cert)


def test_the_temp_file_is_removed_on_ctrl_c(curl, sent, pair, tmp_path):
    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, "pw"))
    sent.boom = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        curl("-E", f"{both}:pw", "https://x.test/")
    assert not os.path.exists(sent.cert)


def test_a_wrong_password_is_58(curl, sent, pair, tmp_path):
    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, "right"))
    with pytest.raises(CommandError) as err:
        curl("-E", f"{both}:Hunter2x", "https://x.test/")
    assert err.value.status == 58
    assert f"could not unlock the key in {both}: wrong password?" in str(err.value)
    assert "Hunter2x" not in str(err.value)  # the password isn't shown
    assert sent.cert is None  # nothing was sent


def test_an_encrypted_key_without_a_password_says_how(curl, sent, pair, tmp_path):
    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, "pw"))
    with pytest.raises(CommandError) as err:
        curl("-E", str(both), "https://x.test/")
    assert err.value.status == 58
    assert "is encrypted" in str(err.value) and "--pass" in str(err.value)


def test_a_plain_key_is_handed_over_as_it_is(curl, sent, pair, tmp_path, monkeypatch):
    import tempfile

    cert, key = pair
    (tmp_path / "c.pem").write_bytes(cert_pem(cert))
    (tmp_path / "c.key").write_bytes(key_pem(key))
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: pytest.fail("no temp file for a plain key"))
    curl("-E", str(tmp_path / "c.pem"), "--key", str(tmp_path / "c.key"), "https://x.test/")
    assert sent.cert == (str(tmp_path / "c.pem"), str(tmp_path / "c.key"))
    both = tmp_path / "both.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key))
    curl("-E", f"{both}:ignored", "https://x.test/")  # a password for a plain key changes nothing
    assert sent.cert == str(both)


def test_a_windows_drive_is_part_of_the_path(monkeypatch):
    assert curl_mod._split_cert(r"C:\certs\me.pem") == (r"C:\certs\me.pem", None)
    assert curl_mod._split_cert(r"C:\certs\me.pem:pw") == (r"C:\certs\me.pem", "pw")
    assert curl_mod._split_cert("me.pem:a:b") == ("me.pem", "a:b")
    monkeypatch.setattr(os.path, "exists", lambda p: p == "odd:name.pem")
    assert curl_mod._split_cert("odd:name.pem") == ("odd:name.pem", None)


def test_a_missing_cert_file_is_58(curl, sent, tmp_path):
    with pytest.raises(CommandError) as err:
        curl("-E", str(tmp_path / "nope.pem"), "https://x.test/")
    assert err.value.status == 58 and "nope.pem" in str(err.value)


def test_pkcs12(curl, sent, pair, tmp_path):
    cert, key = pair
    bundle = tmp_path / "me.p12"
    bundle.write_bytes(pkcs12.serialize_key_and_certificates(
        b"me", key, cert, None, serialization.BestAvailableEncryption(b"pw")))
    curl("-E", f"{bundle}:pw", "https://x.test/")
    assert same_key(unlocked_key(sent), key) and cert_pem(cert) in sent.content
    assert not os.path.exists(sent.cert)
    with pytest.raises(CommandError) as err:
        curl("-E", f"{bundle}:nope", "https://x.test/")
    assert err.value.status == 58 and "wrong password?" in str(err.value)
    renamed = tmp_path / "me.bin"
    renamed.write_bytes(bundle.read_bytes())
    curl("--cert-type", "p12", "-E", str(renamed), "--pass", "pw", "https://x.test/")
    assert same_key(unlocked_key(sent), key)


def test_no_cryptography_is_a_clear_error(curl, sent, pair, tmp_path, monkeypatch):
    cert, key = pair
    both = tmp_path / "client.pem"
    both.write_bytes(cert_pem(cert) + key_pem(key, "pw"))
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.serialization", None)
    with pytest.raises(CommandError) as err:
        curl("-E", f"{both}:pw", "https://x.test/")
    assert err.value.status == 58 and "needs the 'cryptography' package" in str(err.value)
