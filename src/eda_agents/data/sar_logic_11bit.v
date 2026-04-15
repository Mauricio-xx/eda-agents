// 11-bit SAR logic, design_reference.
//
// Generates a 10-cycle conversion from rising edges of `clk`, enabled by
// `En` and gated on (Op ^ Om) to accept only valid comparator decisions.
// Port layout mirrors sar_logic.v (8-bit) so d_cosim wiring from
// generate_sar_adc_11bit_netlist stays regular: B MSB->LSB, BN MSB->LSB,
// D MSB->LSB. Counter rolls from 0 (MSB) through 9 (LSB); the final
// iteration is reported at counter=10 so D accumulates all 10 decisions
// plus an MSB pre-slot at D[10] (kept 0 — matches the 8-bit convention
// where D7 is unused).
//
// Written for eda-agents S7 SAR 11-bit design_reference; not
// silicon-validated. Reviewers: see docs/skills/sar_adc/sar-logic.md.

module sar_logic_11bit (
    input wire clk,
    input wire Op,
    input wire En,
    input wire Om,
    input wire rst,
    output reg [9:0]  B,
    output reg [9:0]  BN,
    output reg [10:0] D
);

    reg [4:0] counter = 5'd0;

    always @(posedge clk) begin
        if (rst) begin
            B       <= 10'd0;
            BN      <= 10'd0;
            D       <= 11'd0;
            counter <= 5'd0;
        end else if (En && (Op ^ Om)) begin
            if (counter < 5'd10) begin
                // Accumulate the decided bit into D (MSB-first when
                // counter=0). D[10] stays 0 (parity with 8-bit D[7]).
                D <= D | ({10'b0, Op} << counter);

                B[counter[3:0]]  <= Op  ? 1'b1 : 1'b0;
                BN[counter[3:0]] <= Om  ? 1'b1 : 1'b0;

                counter <= counter + 5'd1;
            end
        end
    end

endmodule
