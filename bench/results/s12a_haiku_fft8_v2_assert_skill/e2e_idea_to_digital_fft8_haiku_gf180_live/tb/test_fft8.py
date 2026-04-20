import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly


@cocotb.test()
async def test_fft8_basic(dut):
    """FFT8 basic sanity test - validates pipeline structure and latency."""
    # Start clock at 100 MHz (10 ns period)
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    # Reset
    dut.rst_n.value = 0
    dut.in_valid.value = 0
    for sig in [dut.x0, dut.x1, dut.x2, dut.x3, dut.x4, dut.x5, dut.x6, dut.x7]:
        sig.value = 0

    for _ in range(5):
        await RisingEdge(dut.clk)

    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.out_valid.value) == 0, "out_valid should be 0 after reset"

    # Test vector 1: All zeros
    await RisingEdge(dut.clk)
    dut.in_valid.value = 1
    for i in range(8):
        getattr(dut, f"x{i}").value = 0

    for _ in range(4):
        await RisingEdge(dut.clk)

    await ReadOnly()
    assert int(dut.out_valid.value) == 1, "out_valid should be 1 after 4 clocks"
    assert int(dut.X0_re.value) == 0, "All-zeros FFT should have X0_re = 0"
    cocotb.log.info("Vector 1 (all zeros) PASSED")

    # Test vector 2: Impulse
    await RisingEdge(dut.clk)
    dut.in_valid.value = 1
    dut.x0.value = 100
    for i in range(1, 8):
        getattr(dut, f"x{i}").value = 0

    for _ in range(4):
        await RisingEdge(dut.clk)

    await ReadOnly()
    assert int(dut.out_valid.value) == 1, "out_valid should be 1"
    X0_re = int(dut.X0_re.value)
    assert X0_re == 100, f"Impulse FFT should have X0_re ≈ 100, got {X0_re}"
    cocotb.log.info("Vector 2 (impulse) PASSED")

    # Test vector 3: DC (all ones)
    await RisingEdge(dut.clk)
    dut.in_valid.value = 1
    for i in range(8):
        getattr(dut, f"x{i}").value = 1

    for _ in range(4):
        await RisingEdge(dut.clk)

    await ReadOnly()
    assert int(dut.out_valid.value) == 1, "out_valid should be 1"
    X0_re = int(dut.X0_re.value)
    assert X0_re == 8, f"DC FFT should have X0_re = 8, got {X0_re}"
    cocotb.log.info("Vector 3 (DC) PASSED")

    cocotb.log.info("All FFT8 sanity tests PASSED")
