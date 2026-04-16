// 4-bit up-counter with synchronous active-high reset.
//
// Hardened by the bench's LibreLane RTL-to-GDS task
// (bench/tasks/end-to-end/digital_counter_gf180.yaml, gap #5) and
// reused by the post-synthesis gate-level simulation task (gap #2).
// Kept deliberately tiny so the full flow runs in seconds.

module counter (
    input  wire       clk,
    input  wire       rst,
    output wire [3:0] q
);
    reg [3:0] cnt;

    always @(posedge clk) begin
        if (rst)
            cnt <= 4'b0;
        else
            cnt <= cnt + 4'b1;
    end

    assign q = cnt;
endmodule
