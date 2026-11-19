"""Temporary authenticated IPv4 GRE echo for testing a MeshVPS client."""

import argparse
import hashlib
import hmac
import ipaddress
import secrets
import socket
import struct
import time


SIZES = (96, 1280)
NONCE = slice(40, 56)
GRE_PROTOCOL = 47
LISTEN_SECONDS = 300
REPLY_LIMIT = 32


def checksum(header):
    total = sum(struct.unpack('!10H', header))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def frame(secret, nonce, size, response):
    key = hmac.digest(secret, b'meshvps-gre-pc-key-v1', 'sha256')[:4]
    source, target = (2, 1) if response else (1, 2)
    frame_bytes = bytearray(size)
    frame_bytes[:8] = b'\x20\x00\x08\x00' + key
    struct.pack_into('!BBHHHBBH4s4s', frame_bytes, 8, 0x45, 0, size - 8, 0, 0, 64, 17, 0,
                     bytes((198, 18, 254, source)), bytes((198, 18, 254, target)))
    struct.pack_into('!H', frame_bytes, 18, checksum(frame_bytes[8:28]))
    struct.pack_into('!HHHH', frame_bytes, 28, 46320 + source, 46320 + target, size - 28, 0)
    frame_bytes[36:40] = b'MVG1'
    frame_bytes[NONCE] = nonce
    frame_bytes[-32:] = hmac.digest(secret, frame_bytes[:-32], 'sha256')
    return bytes(frame_bytes)


def gre_payload(datagram):
    if len(datagram) < 20 or datagram[0] >> 4 != 4 or datagram[9] != GRE_PROTOCOL:
        return b''
    start = (datagram[0] & 15) * 4
    length = struct.unpack_from('!H', datagram, 2)[0]
    fragments = struct.unpack_from('!H', datagram, 6)[0]
    if start < 20 or length != len(datagram) or fragments & 0x3fff:
        return b''
    return datagram[start:] if length - start in SIZES else b''


def reply_for(datagram, secret, already_seen):
    request = gre_payload(datagram)
    if request[36:40] != b'MVG1':
        return None
    nonce = request[NONCE]
    if nonce in already_seen or not hmac.compare_digest(request, frame(secret, nonce, len(request), False)):
        return None
    already_seen.add(nonce)
    return frame(secret, nonce, len(request), True)


def unicast(text):
    address = ipaddress.IPv4Address(text)
    if address.packed[0] == 0 or address.packed[0] >= 224:
        raise ValueError('Expected a unicast IPv4 address.')
    return address


def primary_ipv4():
    # Connecting a UDP socket selects a source address without sending a packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as routing:
        routing.connect(('192.0.2.1', 9))
        return unicast(routing.getsockname()[0])


def serve(public, local, secret, *, seconds=LISTEN_SECONDS):
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, GRE_PROTOCOL) as channel:
        channel.bind((str(local), 0))
        print(f'meshvps-gre://{secret.hex()}@{public}', flush=True)
        print(f'Listening for GRE (IP protocol 47) for {seconds} s. Ctrl+C ends the test.', flush=True)
        deadline = time.monotonic() + seconds
        seen = set()
        sent = 0
        while len(seen) < REPLY_LIMIT:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            channel.settimeout(remaining)
            try:
                packet, sender = channel.recvfrom(2048)
            except TimeoutError:
                break
            try:
                source = unicast(sender[0])
            except ValueError:
                continue
            answer = reply_for(packet, secret, seen)
            if answer is not None:
                try:
                    channel.sendto(answer, (str(source), 0))
                    sent += 1
                    print(f'Authenticated request: {len(answer)} bytes. Reply sent.', flush=True)
                except OSError as error:
                    print(f'Authenticated request: {len(answer)} bytes. Reply failed: {error}', flush=True)
        print(f'Authenticated requests: {len(seen)}. Replies sent: {sent}.', flush=True)


def main():
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument('--ipv4', help='Public IPv4 to put in the test code if the VPS uses NAT.')
    options.add_argument('--bind', help='Local IPv4 to listen on if the VPS has multiple addresses.')
    args = options.parse_args()
    try:
        local = unicast(args.bind) if args.bind else primary_ipv4()
        public = unicast(args.ipv4) if args.ipv4 else local
        if not public.is_global:
            raise ValueError('Specify the public IPv4 from the hosting panel using --ipv4.')
        serve(public, local, secrets.token_bytes(32))
    except PermissionError:
        options.exit(1, 'The GRE probe needs root or CAP_NET_RAW. Run it from a root VPS console.\n')
    except (OSError, ValueError) as error:
        options.exit(1, f'GRE probe: {error}\n')
    except KeyboardInterrupt:
        print('\nGRE probe stopped.', flush=True)


if __name__ == '__main__':
    main()
