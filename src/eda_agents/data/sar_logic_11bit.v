// 11-bit SAR logic, design_reference.
//
// Resolves 11 bits over 11 `clk` posedges while `En` is high and
// `(Op ^ Om)` asserts (a valid comparator decision). The MSB is
// written to D[0] on the first iteration and the LSB to D[10] on the
// last; extract_enob in sar_adc_11bit.py reads this back as
//   code = sum(D[i] << (10 - i)).
// That "first decision lands at D[0]" convention matches the 8-bit
// sar_logic.v behaviour; it is the one the extract_enob helpers on
// both SAR topologies share.
//
// Counter width is 5 bits (0..10) so a single roll doesn't overflow.
// Written for eda-agents S7 SAR 11-bit design_reference; not
// silicon-validated. Reviewers: see docs/skills/sar_adc/sar-logic.md.

module sar_logic_11bit (
    input wire clk,
    input wire Op,
    input wire En,
    input wire Om,
    input wire rst,
    output reg [10:0] B,
    output reg [10:0] BN,
    output reg [10:0] D
);

    reg [4:0] counter = 5'd0;

    always @(posedge clk) begin
        if (rst) begin
            B       <= 11'd0;
            BN      <= 11'd0;
            D       <= 11'd0;
            counter <= 5'd0;
        end else if (En && (Op ^ Om)) begin
            if (counter < 5'd11) begin
                D <= D | ({10'b0, Op} << counter);
                B[counter[3:0]]  <= Op ? 1'b1 : 1'b0;
                BN[counter[3:0]] <= Om ? 1'b1 : 1'b0;
                counter <= counter + 5'd1;
            end
        end
    end

endmodule
