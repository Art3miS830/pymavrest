import serial
import time
from loguru import logger


def read_response(ser, timeout=5, expected=None):
    start = time.time()
    resp = b''
    while (time.time() - start) < timeout:
        resp += ser.read(ser.in_waiting)
        decoded = resp.decode(errors='ignore')
        if expected and expected in decoded:
            return decoded
        if 'OK' in decoded or 'ERROR' in decoded:
            return decoded
        time.sleep(0.1)
    return resp.decode(errors='ignore')


def send_command(ser, cmd, expected='OK', timeout=5):
    ser.write((cmd + '\r').encode())
    resp = read_response(ser, timeout, expected)
    logger.debug(f"Command {cmd}: {resp}")
    if 'ERROR' in resp or (expected and expected not in resp):
        raise ValueError(f"Failed {cmd}: {resp}")
    return resp


def send_sms_part(ser, number, text):
    resp = send_command(ser, 'AT+CMGF=1')
    if resp is None:
        logger.error(f"send command: 'AT+CMGF=1' to {number} was not successful")
        raise ValueError
    ser.write(f'AT+CMGS="{number}"\r'.encode())
    resp = read_response(ser, 5, expected='>')
    if '>' not in resp:
        raise ValueError("No prompt for message")
    ser.write(text.encode() + b'\x1A')
    resp = read_response(ser, 10)
    if 'OK' not in resp:
        raise ValueError(f"Send failed: {resp}")
    

def send_multipart_sms(port, baudrate, recipient, message, chunk_size=160, timeout: int = 5):
    parts = [message[i:i+chunk_size] for i in range(0, len(message), chunk_size)]
    with serial.Serial(port, baudrate, timeout=timeout) as ser:
        send_command(ser, 'AT')  # Check modem
        for idx, part in enumerate(parts, 1):
            logger.info(f"Sending part {idx}/{len(parts)} to {recipient}")
            send_sms_part(ser, recipient, part)
            time.sleep(1)  # Delay between parts


