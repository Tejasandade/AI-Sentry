from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import datetime
import ipaddress
import os

key_path = os.path.join("d:\\AI_Sentry_Project", "key.pem")
cert_path = os.path.join("d:\\AI_Sentry_Project", "cert.pem")

# Generate a 2048-bit private key
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Generate a self-signed cert
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"AI Sentry"),
    x509.NameAttribute(NameOID.COMMON_NAME, u"192.168.0.105")
])

cert = x509.CertificateBuilder().subject_name(
    subject
).issuer_name(
    issuer
).public_key(
    private_key.public_key()
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.datetime.utcnow() - datetime.timedelta(days=1)
).not_valid_after(
    datetime.datetime.utcnow() + datetime.timedelta(days=3650)
).add_extension(
    x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("192.168.0.105"))]),
    critical=False,
).sign(private_key, hashes.SHA256())

with open(key_path, "wb") as f:
    f.write(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ))
with open(cert_path, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("SSL Certificates successfully generated.")
