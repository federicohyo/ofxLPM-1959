from io import StringIO
from pyftdi.spi import SpiController
from pyftdi.ftdi import Ftdi
import time
from pyftdi.gpio import GpioAsyncController
import sys, os
import binascii

#show all FTDI devices on the USB bus, for debugging purposes:
s = StringIO()
Ftdi.show_devices(out=s)
devlist = s.getvalue().splitlines()[1:-1]
gooddevs = []

print("FTDI devices on USB bus:")
for dev in devlist:
    url = dev.split('(')[0].strip()
    name = '(' + dev.split('(')[1]
    print(f"  {url} : {name}")
    if name == '(Quad RS232-HS)':
        gooddevs.append(url)

class Config:
    FTDI_A = 'ftdi://ftdi:4232h/1'   # channel A -> ADBUSx
    # ADBUS0 is sck
    # ADBUS1 is sdi
    # ADBUS2 is sdo
    # ADBUS3 is cs
    FTDI_B = 'ftdi://ftdi:4232h/2'   # channel B -> BDBUSx
    FTDI_C = 'ftdi://ftdi:4232h/3'   # channel C -> CDBUSx
    # CDBUS0 is led D1 #if low led turns on, if high led turns off
    # CDBUS7 is led D2 #if low led turns on, if high led turns off
    FTDI_D = 'ftdi://ftdi:4232h/4'   # channel D -> DDBUSx 
    # (UART: DDBUS0=RX, DDBUS1=TX)
    


    # Flash/status constants
    SR_WIP = 0b00000001  # Busy/Work-in-progress bit
    SR_WEL = 0b00000010  # Write enable bit
    SR_BP0 = 0b00000100  # bit protect #0
    SR_BP1 = 0b00001000  # bit protect #1
    SR_BP2 = 0b00010000  # bit protect #2
    SR_BP3 = 0b00100000  # bit protect #3
    SR_TBP = SR_BP3      # top-bottom protect bit
    SR_SP = 0b01000000
    SR_BPL = 0b10000000
    SR_PROTECT_NONE = 0  # BP[0..2] = 0
    SR_PROTECT_ALL = 0b00011100  # BP[0..2] = 1
    SR_LOCK_PROTECT = SR_BPL
    SR_UNLOCK_PROTECT = 0
    SR_BPL_SHIFT = 2

    CMD_READ_STATUS = 0x05  # Read status register
    CMD_WRITE_ENABLE = 0x06  # Write enable
    CMD_WRITE_DISABLE = 0x04  # Write disable
    CMD_PROGRAM_PAGE = 0x02  # Write page
    CMD_EWSR = 0x50  # Enable write status register
    CMD_WRSR = 0x01  # Write status register
    CMD_ERASE_SUBSECTOR = 0x20
    CMD_ERASE_HSECTOR = 0x52
    CMD_ERASE_SECTOR = 0xD8
    # CMD_ERASE_CHIP = 0xC7
    CMD_ERASE_CHIP = 0x60
    CMD_RESET_CHIP = 0x99
    CMD_JEDEC_DATA = 0x9f

    CMD_READ_LO_SPEED = 0x03  # Read @ low speed
    CMD_READ_HI_SPEED = 0x0B  # Read @ high speed
    ADDRESS_WIDTH = 3

    #correct GigaDevice SPI NOR flash constants, for reference:
    JEDEC_ID = 0xC8
    DEVICES = {0x40: 'GD25Q', 0x60: 'GD25LQ'}
    SIZES = {0x11: 1 << 17, 0x12: 1 << 18, 0x13: 1 << 19, 0x14: 1 << 20,
             0x15: 2 << 20, 0x16: 4 << 20, 0x17: 8 << 20, 0x18: 16 << 20}
    
    SPI_FREQ_MAX = 104  # MHz
    CMD_READ_UID = 0x4B
    UID_LEN = 0x8  # 64 bits
    READ_UID_WIDTH = 4  # 4 dummy bytes
    TIMINGS = {'page': (0.0015, 0.003),  # 1.5/3 ms
               'subsector': (0.200, 0.200),  # 200/200 ms
               'sector': (1.0, 1.0),  # 1/1 s
               'bulk': (32, 64),  # seconds
               'lock': (0.05, 0.1),  # 50/100 ms
               'chip': (4, 11)}

    # Caravel command constants
    CARAVEL_PASSTHRU = 0xC4
    CARAVEL_STREAM_READ = 0x40
    CARAVEL_STREAM_WRITE = 0x80
    CARAVEL_REG_READ = 0x48
    CARAVEL_REG_WRITE = 0x88

class HKSPI:
    # NOTE: uart_enable_mode is ignored by HKSPI.
    def __init__(self, ftdi_device: str=None, uart_enable_mode: int=None):
        if ftdi_device is not None:
            self.ftdi_device = ftdi_device
        else:
            if len(gooddevs) == 0:
                raise RuntimeError("No suitable FTDI devices found. Please connect a FTDI device and try again.")
            elif len(gooddevs) > 1:
                print("Multiple suitable FTDI devices found. Defaulting to the first one:", gooddevs[0])
            self.ftdi_device = gooddevs[0]
            # gooddevs 0 is channel A, which is what we want for SPI
            # gooddevs 1 is channel B, used for DAC bit-bang lines
            # gooddevs 2 is channel C, used for DAC CS and LEDs
            # gooddevs 3 is channel D, used for UART (DDBUS0/1)

        self.spi = SpiController(cs_count=1)
        self.spi.configure(self.ftdi_device)

        if uart_enable_mode is not None:
            print(
                "HKSPI: uart_enable_mode is ignored. "
                "UART is on FTDI channel D (DDBUS0/1) and does not require a GPIO gate pin."
            )

        self.slave = self.spi.get_port(cs=0, freq=1E6, mode=0)

   
    def identify(self):
        print("Caravel data:")

        mfg = self.slave.exchange([Config.CARAVEL_STREAM_READ, 0x01], 2)
        print("   mfg        = {:04x}".format(int.from_bytes(mfg, byteorder='big')))
        
        product = self.slave.exchange([Config.CARAVEL_REG_READ, 0x03], 1)
        print("   product    = {:02x}".format(int.from_bytes(product, byteorder='big')))
        
        data = self.slave.exchange([Config.CARAVEL_STREAM_READ, 0x04], 4)
        print("   project ID = {:08x}".format(int('{:032b}'.format(int.from_bytes(data, byteorder='big'))[::-1], 2)))
        print("   project ID = {:08x}".format(int.from_bytes(data, byteorder='big')))

        if int.from_bytes(mfg, byteorder='big') != 0x0456:
            print("Incorrect MFG value, expected 0x0456.")
            exit(2)
            
    def get_status(self):
        return int.from_bytes(self.slave.exchange([Config.CARAVEL_PASSTHRU, Config.CMD_READ_STATUS],1), byteorder='big')

    def print_status(self):
        print("status = 0x{:02x}".format(self.get_status()))

    def print_long_status(self):
        jedec = self.flash_read_jedec()
        if jedec[0] == int('c8', 16):
            print("changing cmd values...")
            print("status reg_1 = {}".format(hex(self.get_status())))
        else:
            print("status reg_1 = {}".format(hex(self.get_status())))
            status = self.slave.exchange([Config.CARAVEL_PASSTHRU, 0x35], 1)
            print("status reg_2 = {}".format(hex(int.from_bytes(status, byteorder='big'))))
            # print("status = {}".format(hex(from_bytes(slave.exchange([CMD_READ_STATUS], 2)[1], byteorder='big'))))



    def is_busy(self):
        return self.get_status() & Config.SR_WIP

    def read_reg(self, reg: int):
        return self.slave.exchange([Config.CARAVEL_REG_READ, reg], 1)
    
    def write_reg(self, reg: int, value: int):
        self.slave.exchange([Config.CARAVEL_REG_WRITE, reg, value])

    def read_project_id(self):
        data = self.slave.exchange([Config.CARAVEL_STREAM_READ, 0x04], 4)
        return int('{:032b}'.format(int.from_bytes(data, byteorder='big'))[::-1], 2)

    def cpu_reset_hold(self):
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x0b, 0x01])

    def cpu_reset_release(self):
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x0b, 0x00])

    def cpu_reset_toggle(self):
        self.cpu_reset_hold()
        self.cpu_reset_release()

    def flash_reset(self):
        self.slave.write([Config.CARAVEL_PASSTHRU, Config.CMD_RESET_CHIP])
        self.print_status()

    def flash_read_jedec(self):
        return self.slave.exchange([Config.CARAVEL_PASSTHRU, Config.CMD_JEDEC_DATA], 3)
    
    def flash_identify(self):
        jedec = self.flash_read_jedec()
        print("JEDEC = {:06x}".format(int.from_bytes(jedec, byteorder='big')))

        if jedec[0:1] != bytes.fromhex('c8'):
            print("GigaDevice SPI NOR flash not found")
            exit(1)

    def flash_write_enable(self):
        self.slave.write([Config.CARAVEL_PASSTHRU, Config.CMD_WRITE_ENABLE])

    def flash_erase(self, wait=True, quiet=False):
        if not quiet: print("Erasing chip...")

        self.flash_write_enable()
        self.slave.write([Config.CARAVEL_PASSTHRU, Config.CMD_ERASE_CHIP])

        if wait:
            while (self.is_busy()):
                time.sleep(0.3)
                if not quiet: print('.', end='', flush=True)
                # self.led1.toggle()

            if not quiet: print("done")
            if not quiet: self.print_status()

    def engage_dll(self):
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x08, 0x01])
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x09, 0x00])

    def read_dll_trim(self):
        return self.slave.exchange([Config.CARAVEL_STREAM_READ, 0x0d], 4)
    
    def disengage_dll(self):
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x09, 0x01])
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x08, 0x00])
    
    def dco_mode(self):
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x08, 0x03])
        self.slave.write([Config.CARAVEL_REG_WRITE, 0x09, 0x00])

    # Write the 26 bits of the DCO trim value.
    # `data` is the exact 26-bit pattern to write,
    # where 0x3fff_ffff is the maximum trim (slowest clock)
    # and   0x0000_0000 is the minimum trim (fastest clock).
    # Note that it's the "population count" of bits set to 1 that determines the trim (not
    # the binary value itself). This is known as 'thermometer code' or 'unary coding' and
    # it means there are effectively only 27 distinct values, e.g.
    # ranging from 0b0 through 0b1, 0b11, 0b111... up to 0b11_1111_1111_1111_1111_1111_1111
    def dco_trim(self, value: int):
        #NOTE: DCO trim registers are laid out in little-endian order
        # with the 0x0d register receiving bits [7:0],
        # 0x0e gets [15:8], 0x0f gets [23:16], and 0x10 gets [25:24]:
        bytes_for_regs = list(value.to_bytes(4, byteorder='little'))
        self.slave.exchange([Config.CARAVEL_STREAM_WRITE, 0x0d] + bytes_for_regs)


    def __enter__(self):
        return self

    def __exit__(self, *_):
        if hasattr(self.spi, 'close'): self.spi.close()
        else: self.spi.terminate()
        
        return False      
            
class AD5664RBitBang:
    # BDBUS pins
    PIN_CLK = 0      # BDBUS0
    PIN_DATA = 1     # BDBUS1
    PIN_FORCE_OUT = 5  # BDBUS5 currenlty connedted to BDBUS1

    UART_ENABLE = 0
    UART_DISABLE = 1
    UART_DEFAULT = 2
    
    # Mapping the DAC to their respective CDBUS(1..6)
    DAC_TO_CDBUS = {
        1: 1,
        2: 2,
        3: 3,
        4: 4,
        5: 5,
        6: 6,
    }

    # LED pins on CDBUS
    PIN_LED0 = 0
    PIN_LED7 = 7

    def __init__(
        self,
        url_b: str,
        url_c: str,
        frequency: int = 100_000,
        cs_active_low: bool = True,
        uart_enable_mode: int = UART_DEFAULT,
    ):
        self._gpio_b = GpioAsyncController()
        self._gpio_c = GpioAsyncController()
        self._cs_active_low = cs_active_low # If True, CS lines are active LOW; needed to write AD5664R

        #setup directions: BDBUS0,1,5 as CLK, DATA, FORCE_OUT; CDBUS1..6 for DAC CS; CDBUS0 and CDBUS7 for LEDs
        self._b_dir = (1 << self.PIN_CLK) | (1 << self.PIN_DATA) | (1 << self.PIN_FORCE_OUT)
        self._cs_mask = sum(1 << pin for pin in self.DAC_TO_CDBUS.values())
        self._led_mask = (1 << self.PIN_LED0) | (1 << self.PIN_LED7)
        self._c_dir = self._cs_mask | self._led_mask

        # Configure GPIOs
        self._gpio_b.configure(url_b, direction=self._b_dir, frequency=frequency)
        self._gpio_c.configure(url_c, direction=self._c_dir, frequency=frequency)

        # Initialize states: CLK=0, DATA=0, all CS inactive, LEDs off
        self._b_state = 0x00
        self._c_state = self._all_cs_inactive_state() | self._led_off_state()
        self._apply_uart_enable_mode(uart_enable_mode)
        self._gpio_b.write(self._b_state)
        self._gpio_c.write(self._c_state)

    def _apply_uart_enable_mode(self, uart_enable_mode: int) -> None:
        if uart_enable_mode not in (self.UART_ENABLE, self.UART_DISABLE, self.UART_DEFAULT):
            raise ValueError(f'Invalid uart_enable_mode {uart_enable_mode}')
        # Backward compatibility: legacy UART gate control mode is now a no-op.
        self._uart_enable_mode = uart_enable_mode

    def set_uart_enable_mode(self, uart_enable_mode: int) -> None:
        self._apply_uart_enable_mode(uart_enable_mode)
    
    def _all_cs_inactive_state(self) -> int:
        if self._cs_active_low:
            return self._cs_mask
        return 0x00

    def _led_off_state(self) -> int:
        return 0x00

    # set CLK and DATA lines and write to GPIO B
    def _set_clk_data(self, clk: int, data: int) -> None:
        clk_mask = 1 << self.PIN_CLK
        data_mask = 1 << self.PIN_DATA
        force_out_mask = 1 << self.PIN_FORCE_OUT ## remove when not connected anymore

        if clk:
            self._b_state |= clk_mask
        else:
            self._b_state &= ~clk_mask

        if data:
            self._b_state |= data_mask
            self._b_state |= force_out_mask ## remove when not connected anymore
        else:
            self._b_state &= ~data_mask
            self._b_state &= ~force_out_mask ## remove when not connected anymore

        self._gpio_b.write(self._b_state)

    # Select the DAC by activating its CS line and deactivating all others
    def _select_dac(self, dac_id: int) -> None:
        if dac_id not in self.DAC_TO_CDBUS:
            raise ValueError(f'Invalid DAC id {dac_id}, use 1..6')

        cs_pin = self.DAC_TO_CDBUS[dac_id]
        cs_mask = 1 << cs_pin
        led_state = self._c_state & self._led_mask
        self._c_state = self._all_cs_inactive_state() | led_state

        if self._cs_active_low:
            self._c_state &= ~cs_mask
        else:
            self._c_state |= cs_mask

        self._gpio_c.write(self._c_state)

    def _deselect_all(self) -> None:
        led_state = self._c_state & self._led_mask
        self._c_state = self._all_cs_inactive_state() | led_state
        self._gpio_c.write(self._c_state)

    def set_leds(self, led0: bool = None, led7: bool = None) -> None:
        """Set LED states. Pass led0=True/False and/or led7=True/False."""
        if led0 is not None:
            led_mask = 1 << self.PIN_LED0
            if led0:
                self._c_state |= led_mask
            else:
                self._c_state &= ~led_mask
        
        if led7 is not None:
            led_mask = 1 << self.PIN_LED7
            if led7:
                self._c_state |= led_mask
            else:
                self._c_state &= ~led_mask
        
        self._gpio_c.write(self._c_state)

    # Shift out a 24-bit frame MSB first on CLK and DATA lines. DAC samples on falling edge.
    def _shift_out(self, value: int, bits: int = 24, half_period_s: float = 1e-6) -> None:
        for bit_index in range(bits - 1, -1, -1):
            bit = (value >> bit_index) & 0x1
            self._set_clk_data(0, bit)  # Set data while CLK low
            if half_period_s:
                time.sleep(half_period_s)  # Data setup time
            self._set_clk_data(1, bit)  # CLK rising edge
            if half_period_s:
                time.sleep(half_period_s)
            self._set_clk_data(0, bit)  # CLK falling edge - DAC samples here
            if half_period_s:
                time.sleep(half_period_s)  # Data hold time after falling edge

    def write_dac(self, dac_id: int, command: int, address: int, data_16: int, half_period_s: float = 1e-6) -> None:
        if not (0 <= command <= 0x7):
            raise ValueError(f'Invalid command {command:#x}, use 0x0..0x7')
        if not (0 <= address <= 0x7):
            raise ValueError(f'Invalid address {address:#x}, use 0x0..0x7')
        if not (0 <= data_16 <= 0xFFFF):
            raise ValueError(f'Invalid data {data_16:#x}, use 0x0000..0xFFFF')

        # AD5664R 24-bit frame: [2-bit don't care][3-bit command][3-bit address][16-bit data]
        frame = ((command & 0b111) << 19) | ((address & 0b111) << 16) | (data_16 & 0xFFFF)
        print(f'DAC{dac_id} frame: {frame:024b} (0x{frame:06X})') ## remove if not needed for debugging

        self._select_dac(dac_id)
        if half_period_s:
            time.sleep(half_period_s)  # CS setup time before first clock edge
        self._shift_out(frame, bits=24, half_period_s=half_period_s)
        time.sleep(half_period_s)
        self._deselect_all()

    def close(self) -> None:
        self._set_clk_data(0, 0)
        self._deselect_all()
        self._gpio_b.close()
        self._gpio_c.close()

    def drive_static(self, data: int, clk: int = 0, duration_s: float = 3.0) -> None:
        """Drive DATA/CLK to fixed levels for probing with DMM/scope."""
        if data not in (0, 1):
            raise ValueError(f'Invalid DATA level {data}, use 0 or 1')
        if clk not in (0, 1):
            raise ValueError(f'Invalid CLK level {clk}, use 0 or 1')

        self._deselect_all()
        self._set_clk_data(clk, data)
        print(f'Driving DATA={data}, CLK={clk} for {duration_s} seconds')
        time.sleep(duration_s)
        self._set_clk_data(0, 0)
        print('Restored DATA=0, CLK=0')

    def pulse_dac_cs_low(self, dac_id: int, duration_s: float = 3.0) -> None:  ## test pulse selected DAC CS low
        if dac_id not in self.DAC_TO_CDBUS:
            raise ValueError(f'Invalid DAC id {dac_id}, use 1..6')

        cs_pin = self.DAC_TO_CDBUS[dac_id]
        cs_mask = 1 << cs_pin
        previous_state = self._c_state
        self._c_state = self._all_cs_inactive_state() | (previous_state & self._led_mask)
        self._c_state &= ~cs_mask
        self._gpio_c.write(self._c_state)
        print(f'DAC {dac_id} CS (CDBUS{cs_pin}) set LOW for {duration_s} seconds')
        time.sleep(duration_s)
        self._c_state = previous_state
        self._gpio_c.write(self._c_state)
        print(f'DAC {dac_id} CS restored to previous state')
        
    
    def voltage_to_hex(self, voltage, vref=1.8):
        if not (0 <= voltage <= vref):
            raise ValueError(f"Voltage must be between 0 and {vref} V")
    
        value = round((voltage / vref) * 65535)
        return f"0x{value:04X}"
    
    def set_dac_voltage(self, dac_id: int, voltage: float, vref: float = 1.8, half_period_s: float = 1e-6, address: int = 0b000) -> None:
        data_16 = self.voltage_to_hex(voltage, vref)
        self.write_dac(dac_id=dac_id, command=0b011, address=address, data_16=int(data_16, 16), half_period_s=half_period_s)

    def power_down_dac(self, dac_id: int, address: int, mode: int = 0b11, half_period_s: float = 1e-6) -> None:
        """Power down a DAC channel to high-Z (or resistive to GND).
        mode: 00=normal, 01=1k to GND, 10=100k to GND, 11=high-Z (tri-state)
        address: 0=A, 1=B, 2=C, 3=D
        """
        # AD5664R power-down register format (Table 11):
        #   data[5:4] = PD1,PD0 (power-down mode for selected channels)
        #   data[3]   = DAC D select
        #   data[2]   = DAC C select
        #   data[1]   = DAC B select
        #   data[0]   = DAC A select
        dac_select = 1 << address
        data_16 = (mode << 4) | dac_select
        self.write_dac(dac_id=dac_id, command=0b100, address=0b000, data_16=data_16, half_period_s=half_period_s)
        print(f'  DAC{dac_id} ch {address}: powered down (mode={mode:#04b}, select={dac_select:#06b})')


def parse_cli_args(argv):
    run_hk = 1
    run_dac = 1
    hex_file = None

    for arg in argv:
        if '=' in arg:
            key, value = arg.split('=', 1)
            key = key.strip().upper()
            value = value.strip()

            if key in ('HK', 'DAC'):
                if value not in ('0', '1'):
                    raise ValueError(f'Invalid value for {key}: {value}. Use 0 or 1.')
                if key == 'HK':
                    run_hk = int(value)
                else:
                    run_dac = int(value)
            else:
                print(f'Ignoring unknown argument: {arg}')
        else:
            if arg.lower().endswith('.hex'):
                hex_file = arg
            else:
                print(f'Ignoring unknown argument: {arg}')

    if hex_file is not None and not os.path.isfile(hex_file):
        print('File not found.')
        sys.exit()

    return run_hk, run_dac, hex_file
        
if __name__ == '__main__':
    run_hk, run_dac, hex_file = parse_cli_args(sys.argv[1:])

    if hex_file is not None:
        print(f'HEX file argument: {hex_file}')

    if run_hk:
        with HKSPI(ftdi_device=Config.FTDI_A) as hk:
            hk.identify()
            hk.cpu_reset_hold()
            print(" ")
            print("Resetting Flash...")
            hk.flash_reset()
            hk.flash_identify()
            hk.flash_erase()
            
            buf = bytearray()
            addr = 0
            nbytes = 0
            total_bytes = 0

            with open(hex_file, mode='r') as f:
                x = f.readline()
                while x != '':
                    if x[0] == '@':
                        addr = int(x[1:],16)
                        print('setting address to {}'.format(hex(addr)))
                    else:
                        # print(x)
                        values = bytearray.fromhex(x[0:len(x)-1])
                        buf[nbytes:nbytes] = values
                        nbytes += len(values)
                        # print(binascii.hexlify(values))

                    x = f.readline()

                    if nbytes >= 256 or (x != '' and x[0] == '@' and nbytes > 0):
                        total_bytes += nbytes
                        # print('\n----------------------\n')
                        # print(binascii.hexlify(buf))
                        # print("\ntotal_bytes = {}".format(total_bytes))

                        hk.flash_write_enable()
                        wcmd = bytearray((Config.CARAVEL_PASSTHRU, Config.CMD_PROGRAM_PAGE,(addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                        # wcmd = bytearray((CARAVEL_PASSTHRU, CMD_WRITE_ENABLE, CMD_PROGRAM_PAGE,(addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                        # print(binascii.hexlify(wcmd))
                        # wcmd.extend(buf[0:255])
                        wcmd.extend(buf)
                        hk.slave.exchange(wcmd)
                        while (hk.is_busy()):
                            time.sleep(0.1)

                        print("addr {}: flash page write successful".format(hex(addr)))

                        if nbytes > 256:
                            buf = buf[255:]
                            addr += 256
                            nbytes -= 256
                            print("*** over 256 hit")
                        else:
                            buf = bytearray()
                            addr += 256
                            nbytes =0

                if nbytes > 0:
                    total_bytes += nbytes
                    # print('\n----------------------\n')
                    # print(binascii.hexlify(buf))
                    # print("\nnbytes = {}".format(nbytes))

                    hk.flash_write_enable()
                    wcmd = bytearray((Config.CARAVEL_PASSTHRU, Config.CMD_PROGRAM_PAGE, (addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                    # wcmd = bytearray((Config.CARAVEL_PASSTHRU, Config.CMD_WRITE_ENABLE, Config.CMD_PROGRAM_PAGE, (addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                    wcmd.extend(buf)
                    hk.slave.exchange(wcmd)
                    while (hk.is_busy()):
                        time.sleep(0.1)

                    print("addr {}: flash page write successful".format(hex(addr)))

            print("\ntotal_bytes = {}".format(total_bytes))

            hk.print_long_status()

            print("************************************")
            print("verifying...")
            print("************************************")

            buf = bytearray()
            addr = 0
            nbytes = 0
            total_bytes = 0

            while (hk.is_busy()):
                time.sleep(0.5)

            # slave.write([CARAVEL_REG_WRITE, 0x0b, 0x01])
            # slave.write([CARAVEL_REG_WRITE, 0x0b, 0x00])

            hk.print_long_status()

            with open(hex_file, mode='r') as f:
                x = f.readline()
                while x != '':
                    if x[0] == '@':
                        addr = int(x[1:],16)
                        print('setting address to {}'.format(hex(addr)))
                    else:
                        # print(x)
                        values = bytearray.fromhex(x[0:len(x)-1])
                        buf[nbytes:nbytes] = values
                        nbytes += len(values)
                        # print(binascii.hexlify(values))

                    x = f.readline()

                    if nbytes >= 256 or (x != '' and x[0] == '@' and nbytes > 0):

                        total_bytes += nbytes
                        # print('\n----------------------\n')
                        # print(binascii.hexlify(buf))
                        # print("\ntotal_bytes = {}".format(total_bytes))

                        read_cmd = bytearray((Config.CARAVEL_PASSTHRU, Config.CMD_READ_LO_SPEED,(addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                        # print(binascii.hexlify(read_cmd))
                        buf2 = hk.slave.exchange(read_cmd, nbytes)
                        if buf == buf2:
                            print("addr {}: read compare successful".format(hex(addr)))
                        else:
                            print("addr {}: *** read compare FAILED ***".format(hex(addr)))
                            print(binascii.hexlify(buf))
                            print("<----->")
                            print(binascii.hexlify(buf2))

                        if nbytes > 256:
                            buf = buf[255:]
                            addr += 256
                            nbytes -= 256
                            print("*** over 256 hit")
                        else:
                            buf = bytearray()
                            addr += 256
                            nbytes =0

                if nbytes > 0:
                    total_bytes += nbytes
                    # print('\n----------------------\n')
                    # print(binascii.hexlify(buf))
                    # print("\nnbytes = {}".format(nbytes))

                    read_cmd = bytearray((Config.CARAVEL_PASSTHRU, Config.CMD_READ_LO_SPEED, (addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff))
                    # print(binascii.hexlify(read_cmd))
                    buf2 = hk.slave.exchange(read_cmd, nbytes)
                    if buf == buf2:
                        print("addr {}: read compare successful".format(hex(addr)))
                    else:
                        print("addr {}: *** read compare FAILED ***".format(hex(addr)))
                        print(binascii.hexlify(buf))
                        print("<----->")
                        print(binascii.hexlify(buf2))

            print("\ntotal_bytes = {}".format(total_bytes))

            print("pll_trim = {}\n".format(binascii.hexlify(hk.read_dll_trim())))

            # print("Setting trim values...\n")
            # slave.write([CARAVEL_REG_WRITE, 0x04, 0x7f])

            # pll_trim = slave.exchange([CARAVEL_REG_READ, 0x04],1)
            # print("pll_trim = {}\n".format(binascii.hexlify(pll_trim)))

            hk.cpu_reset_release()

    if run_dac:
        dac = AD5664RBitBang(
            Config.FTDI_B,
            Config.FTDI_C,
            frequency=200_000,
            cs_active_low=True,
            uart_enable_mode=AD5664RBitBang.UART_DISABLE,
        )

        try:
            dac.set_leds(led0=True, led7=False)
            # Static line test: DATA
            # dac.drive_static(data=1, clk=1, duration_s=7.0)
            # CS pulse test: DAC CS
            # dac.pulse_dac_cs_low(test_dac_id, 5.0)

            print(f'Begin DAC Programming')
            time.sleep(1)
            
            VREFN = 0.9,
            VREFP = 1.8,
            ## command 111 is on/off internal 011 is write update DAC, 000 write DAC, 001 update DAC
            #Address 111 is all DACS, 000 = a, 001 = b, 010 = c, 011 = d

            # dac.set_dac_voltage(dac_id=1, voltage=0.8, vref=VREFP, half_period_s=2e-6, address=0b000) #free
            # dac.set_dac_voltage(dac_id=1, voltage=0.79, vref=VREFP, half_period_s=2e-6, address=0b001) #Vtaup
            # dac.set_dac_voltage(dac_id=1, voltage=0.645, vref=VREFP, half_period_s=2e-6, address=0b010) #Vthrdp
            # dac.set_dac_voltage(dac_id=1, voltage=1.1, vref=VREFP, half_period_s=2e-6, address=0b011) #VEpulseextp

            # dac.set_dac_voltage(dac_id=2, voltage=0.2, vref=VREFN, half_period_s=2e-6, address=0b000) #Vleakn
            # dac.set_dac_voltage(dac_id=2, voltage=0.3, vref=VREFN, half_period_s=2e-6, address=0b001) #Vtaun
            # dac.set_dac_voltage(dac_id=2, voltage=1.78, vref=VREFP, half_period_s=2e-6, address=0b010) #VIpulseextn
            # dac.set_dac_voltage(dac_id=2, voltage=1.78, vref=VREFP, half_period_s=2e-6, address=0b011) #JinhWp0

            dac.set_dac_voltage(dac_id=3, voltage=0.8, vref=VREFP, half_period_s=2e-6, address=0b000) #1.1 IREF_monout disable when you want to monitor
            # dac.set_dac_voltage(dac_id=3, voltage=1.78, vref=VREFP, half_period_s=2e-6, address=0b001) #JinhWp1
            # dac.set_dac_voltage(dac_id=3, voltage=1.78, vref=VREFP, half_period_s=2e-6, address=0b010) #JinhWp2
            # dac.set_dac_voltage(dac_id=3, voltage=1.78, vref=VREFP, half_period_s=2e-6, address=0b011) #JinhWp3

            # dac.set_dac_voltage(dac_id=4, voltage=0.00, vref=VREFN, half_period_s=2e-6, address=0b000) #JexcWn0
            # dac.set_dac_voltage(dac_id=4, voltage=0.00, vref=VREFN, half_period_s=2e-6, address=0b001) #JexcWn1
            # dac.set_dac_voltage(dac_id=4, voltage=0.00, vref=VREFN, half_period_s=2e-6, address=0b010) #JexcWn2
            # dac.set_dac_voltage(dac_id=4, voltage=0.00, vref=VREFN, half_period_s=2e-6, address=0b011) #JexcWn3

            dac.set_dac_voltage(dac_id=5, voltage=0.8, vref=VREFP, half_period_s=2e-6, address=0b000) #VREF 0.8
            dac.set_dac_voltage(dac_id=5, voltage=0.79, vref=VREFP, half_period_s=2e-6, address=0b001) #VB1 0.79
            dac.set_dac_voltage(dac_id=5, voltage=0.645, vref=VREFP, half_period_s=2e-6, address=0b010) #VB2 0.645
            dac.set_dac_voltage(dac_id=5, voltage=1.1, vref=VREFP, half_period_s=2e-6, address=0b011) #TUNEp 1.1

            # dac.set_dac_voltage(dac_id=6, voltage=0.2, vref=VREFP, half_period_s=2e-6, address=0b000) #Buffermonp
            # dac.set_dac_voltage(dac_id=6, voltage=0.34, vref=VREFN, half_period_s=2e-6, address=0b001) #Vrefn
            # dac.set_dac_voltage(dac_id=6, voltage=0.3, vref=VREFP, half_period_s=2e-6, address=0b010) #Vthredp
            # dac.set_dac_voltage(dac_id=6, voltage=1.4, vref=VREFP, half_period_s=2e-6, address=0b011) #Ifdcp

            print(f'programmed the DACs, now turning on LEDs and waiting for 5 seconds before exiting')
            dac.set_leds(led0=False, led7=False)
            time.sleep(1)

        finally:
            dac.close()

    if not run_hk and not run_dac:
        print('Both HK and DAC are disabled (HK=0 DAC=0). Nothing to do.')

# spi = SpiController()

# spi.configure(FTDI_A)
# slave1 = spi.get_port(cs=0, freq=1E6, mode=0) #1E6 for debugging, 10E6 normal operation

# # Read manufacturer ID (2 byte)
# cmd = 0x40 | (2 << 3)
# addr = 0x01
# manufacturer_id = slave1.exchange([cmd, addr], 2)
# print("expected ID = 0456, Returned Manufacturer ID:", manufacturer_id.hex())

# # read product ID (1 byte)
# cmd = 0x40 | (1 << 3)
# addr = 0x03
# product_id = slave1.exchange([cmd, addr], 1)
# print("expected ID = 10, Returned Product ID:", product_id.hex())

# # Read project ID (4 bytes)
# cmd = 0x40 | (4 << 3)
# addr = 0x04
# project_id = slave1.exchange([cmd, addr], 4)
# project_id_hex = ''.join(f"{b:02x}" for b in project_id)
# print("Expected ID = 240996b7, Returned Caravel Project ID:", project_id_hex)
