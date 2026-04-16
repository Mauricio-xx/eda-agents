// Gate-level sim testbench for the 4-bit counter. Consumed by
// bench/tasks/end-to-end/gl_sim_post_synth_counter.yaml (gap #2).
//
// The GL sim runner compiles the LibreLane-produced *.nl.v netlist
// against this TB and runs it through iverilog+vvp. PASS/FAIL is
// communicated via $display so the adapter can regex-match the log.

`timescale 1ns/1ps

module tb_counter;
    reg clk;
    reg rst;
    wire [3:0] q;

    counter dut (
        .clk(clk),
        .rst(rst),
        .q(q)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;  // 100 MHz

    integer errors;
    integer i;
    reg [3:0] prev_q;

    initial begin
        errors = 0;
        rst = 1'b1;
        // Hold reset past the first two rising edges so the counter
        // starts from zero regardless of clock alignment.
        #22;
        rst = 1'b0;

        // Sample 10 successive rising edges (fewer than the 16-cycle
        // wrap point) and assert each sample is strictly one greater
        // than the previous one, mod 16. This catches stuck-at-reset,
        // double-increment and off-by-one failures that a simple
        // "not stuck at zero" check would miss.
        prev_q = 4'b0;
        for (i = 0; i < 10; i = i + 1) begin
            @(posedge clk);
            #1;
            if (q !== ((prev_q + 4'b1) & 4'hF)) begin
                $display("CYCLE%0d_FAIL expected=%0d got=%0d",
                         i, (prev_q + 4'b1) & 4'hF, q);
                errors = errors + 1;
            end
            prev_q = q;
        end

        if (errors == 0)
            $display("PASS");
        else
            $display("TB_FAIL errors=%0d", errors);

        $finish;
    end
endmodule
