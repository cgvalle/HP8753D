#!/usr/bin/env python3
"""Connection test for the HP 8753D (GPIB address 16, TALKER/LISTENER).

Examples:
    python hp8753d_connect.py                       # find the analyzer and query *IDN?
    python hp8753d_connect.py --resource "GPIB0::16::INSTR"
"""
import argparse
import sys

import pyvisa

GPIB_ADDR = 16


def resource_manager():
    if sys.platform == 'win32':
        return pyvisa.ResourceManager()  # NI-VISA
    return pyvisa.ResourceManager('@py')  # pyvisa-py + libusb


def find_resource(rm):
    found = rm.list_resources()
    print('VISA resources:', ', '.join(found) or '(none)')
    for r in found:
        # xyphro UsbGpib names the USB resource after the instrument's *IDN?
        if '8753' in r or (r.upper().startswith('GPIB') and r.split('::')[1] == str(GPIB_ADDR)):
            return r
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--resource', help='VISA resource string (default: auto-detect)')
    args = ap.parse_args()

    rm = resource_manager()
    resource = args.resource or find_resource(rm)
    if not resource:
        sys.exit('HP 8753D not found. Check that the analyzer is on, set to TALKER/LISTENER '
                 'at GPIB address %d, and that the adapter is plugged in.' % GPIB_ADDR)

    print('Connecting to', resource)
    try:
        v = rm.open_resource(resource)
        v.timeout = 10000
        print('IDN? ->', v.query('*IDN?').strip())
        v.close()
    except (pyvisa.errors.VisaIOError, ValueError) as e:
        sys.exit('GPIB error: %s\n(if it keeps timing out, replug the adapter)' % e)


if __name__ == '__main__':
    main()
