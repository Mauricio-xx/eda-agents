// Minimal 3-bit counter for the eda-agents AMS four-domain demo.
// Increments on rising edge of clk; synchronous active-high reset.
// Compiled by Verilator into a d_cosim-loadable .so by
// eda_agents.utils.vlnggen.compile_verilog.

module rtl_counter3 (
    input  clk,
    input  rst,
    output q0,
    output q1,
    output q2
);
    reg [2:0] c;

    always @(posedge clk or posedge rst) begin
        if (rst)
            c <= 3'b0;
        else
            c <= c + 3'b1;
    end

    assign q0 = c[0];
    assign q1 = c[1];
    assign q2 = c[2];
endmodule
