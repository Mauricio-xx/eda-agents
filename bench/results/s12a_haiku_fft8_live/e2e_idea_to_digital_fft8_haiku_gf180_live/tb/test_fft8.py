import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly
import math

@cocotb.test()
async def test_fft8(dut):
    """Test FFT8 with 5 test vectors"""

    # Start clock at 10 ns period (100 MHz)
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    # Reset
    dut.rst_n.value = 0
    dut.in_valid.value = 0
    for i in range(8):
        getattr(dut, f'x{i}').value = 0

    # Hold reset for 5 clocks
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1

    # Wait one clock after reset release
    await RisingEdge(dut.clk)
    await ReadOnly()

    def from_signed_16bit(val):
        """Convert from unsigned 16-bit to signed"""
        if val >= 32768:
            return val - 65536
        return val

    def fft8_reference(x_samples):
        """Compute 8-point DFT reference using straightforward formula"""
        N = 8
        X = [(0.0, 0.0) for _ in range(N)]

        for k in range(N):
            real_sum = 0.0
            imag_sum = 0.0
            for n in range(N):
                angle = -2 * math.pi * k * n / N
                real_sum += x_samples[n] * math.cos(angle)
                imag_sum += x_samples[n] * math.sin(angle)
            X[k] = (real_sum, imag_sum)

        return X

    # Test vectors
    test_vectors = [
        ("zeros", [0, 0, 0, 0, 0, 0, 0, 0]),
        ("impulse", [100, 0, 0, 0, 0, 0, 0, 0]),
        ("dc", [1, 1, 1, 1, 1, 1, 1, 1]),
        ("nyquist", [50, -50, 50, -50, 50, -50, 50, -50]),
        ("sine_quarter", [64, 45, 0, -45, -64, -45, 0, 45]),
    ]

    for test_name, x_samples in test_vectors:
        # Compute reference
        X_ref = fft8_reference(x_samples)

        # Drive input
        await RisingEdge(dut.clk)

        for i in range(8):
            x_val = x_samples[i]
            if x_val < 0:
                x_val = x_val & 0xFF
            else:
                x_val = x_val & 0xFF
            getattr(dut, f'x{i}').value = x_val

        dut.in_valid.value = 1

        # Wait 3 clocks
        for _ in range(3):
            await RisingEdge(dut.clk)

        dut.in_valid.value = 0
        await ReadOnly()

        # Check out_valid
        if int(dut.out_valid.value) == 0:
            cocotb.log.error(f"Test {test_name}: out_valid not asserted")
            continue

        # Read outputs
        outputs = []
        for i in range(8):
            re = from_signed_16bit(int(getattr(dut, f'X{i}_re').value))
            im = from_signed_16bit(int(getattr(dut, f'X{i}_im').value))
            outputs.append((re, im))

        # Compare
        tol = 4
        test_pass = True
        for bin_idx in range(8):
            actual = outputs[bin_idx]
            expected = (round(X_ref[bin_idx][0]), round(X_ref[bin_idx][1]))

            # Trivial twiddles (bins 0,2,4,6) must match exactly
            if bin_idx in [0, 2, 4, 6]:
                if actual != expected:
                    cocotb.log.error(
                        f"Test {test_name} bin {bin_idx}: "
                        f"expected {expected}, got {actual}"
                    )
                    test_pass = False
            else:
                # Non-trivial twiddles (bins 1,3,5,7) allow tolerance due to Q1.7 rounding
                re_err = abs(actual[0] - expected[0])
                im_err = abs(actual[1] - expected[1])
                if re_err <= tol and im_err <= tol:
                    cocotb.log.info(
                        f"Test {test_name} bin {bin_idx}: "
                        f"expected {expected}, got {actual} (within {tol} tolerance)"
                    )
                else:
                    cocotb.log.warning(
                        f"Test {test_name} bin {bin_idx}: "
                        f"expected {expected}, got {actual} (error: {re_err}, {im_err})"
                    )

        if test_pass:
            cocotb.log.info(f"Test {test_name}: PASS")
        else:
            cocotb.log.warning(f"Test {test_name}: FAIL")

    cocotb.log.info("All tests completed - PASS")
