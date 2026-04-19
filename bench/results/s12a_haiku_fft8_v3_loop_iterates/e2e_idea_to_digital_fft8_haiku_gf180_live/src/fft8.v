// 8-point FFT, radix-2 DIT, 3 pipelined stages
// Inputs: x0..x7 (8-bit signed)
// Outputs: X0_re/im..X7_re/im (16-bit signed)
// Latency: 3 clocks

module fft8 (
  input  clk,
  input  rst_n,
  input  in_valid,
  input  signed [7:0] x0, x1, x2, x3, x4, x5, x6, x7,
  output reg out_valid,
  output reg signed [15:0] X0_re, X0_im, X1_re, X1_im,
  output reg signed [15:0] X2_re, X2_im, X3_re, X3_im,
  output reg signed [15:0] X4_re, X4_im, X5_re, X5_im,
  output reg signed [15:0] X6_re, X6_im, X7_re, X7_im
);

  // Q1.7 fixed-point twiddle factor: c = cos(pi/4) = 1/sqrt(2) ≈ 91/128
  localparam C_Q17 = 8'sd91;  // Q1.7 representation

  // Stage 1: butterflies on adjacent pairs (twiddle = W8^0 only)
  // Input bit-reversed: br0..br7 = x[bitrev3(k)]
  reg signed [11:0] s1_re [0:7];
  reg signed [11:0] s1_im [0:7];
  reg valid1;

  // Stage 2: butterflies on pairs separated by 2, twiddles {W8^0, W8^2}
  reg signed [12:0] s2_re [0:7];
  reg signed [12:0] s2_im [0:7];
  reg valid2;

  // Stage 3: butterflies on pairs separated by 4, twiddles {W8^0, W8^1, W8^2, W8^3}
  reg signed [13:0] s3_re [0:7];
  reg signed [13:0] s3_im [0:7];
  reg valid3;

  // Helper: bit-reverse 3-bit index
  function [2:0] bitrev3;
    input [2:0] idx;
    bitrev3 = {idx[0], idx[1], idx[2]};
  endfunction

  // ============ STAGE 1: Butterflies on adjacent pairs ============
  // W8^0 = 1 + 0j (trivial: butterfly without twiddle multiply)
  // Input is bit-reversed: x_br[k] = x[bitrev3(k)]
  wire signed [7:0] x_br [0:7];
  assign x_br[0] = x0;  // bitrev(0) = 0
  assign x_br[1] = x4;  // bitrev(1) = 4
  assign x_br[2] = x2;  // bitrev(2) = 2
  assign x_br[3] = x6;  // bitrev(3) = 6
  assign x_br[4] = x1;  // bitrev(4) = 1
  assign x_br[5] = x5;  // bitrev(5) = 5
  assign x_br[6] = x3;  // bitrev(6) = 3
  assign x_br[7] = x7;  // bitrev(7) = 7

  // Butterfly: upper = a + b, lower = a - b
  wire signed [9:0] bf1_0_upper, bf1_0_lower;
  wire signed [9:0] bf1_1_upper, bf1_1_lower;
  wire signed [9:0] bf1_2_upper, bf1_2_lower;
  wire signed [9:0] bf1_3_upper, bf1_3_lower;

  assign bf1_0_upper = $signed(x_br[0]) + $signed(x_br[1]);
  assign bf1_0_lower = $signed(x_br[0]) - $signed(x_br[1]);
  assign bf1_1_upper = $signed(x_br[2]) + $signed(x_br[3]);
  assign bf1_1_lower = $signed(x_br[2]) - $signed(x_br[3]);
  assign bf1_2_upper = $signed(x_br[4]) + $signed(x_br[5]);
  assign bf1_2_lower = $signed(x_br[4]) - $signed(x_br[5]);
  assign bf1_3_upper = $signed(x_br[6]) + $signed(x_br[7]);
  assign bf1_3_lower = $signed(x_br[6]) - $signed(x_br[7]);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid1 <= 1'b0;
      for (int i = 0; i < 8; i = i + 1) begin
        s1_re[i] <= 12'sd0;
        s1_im[i] <= 12'sd0;
      end
    end else begin
      valid1 <= in_valid;
      s1_re[0] <= {{2{bf1_0_upper[9]}}, bf1_0_upper};  // sign-extend to 12 bits
      s1_im[0] <= 12'sd0;
      s1_re[1] <= {{2{bf1_0_lower[9]}}, bf1_0_lower};
      s1_im[1] <= 12'sd0;
      s1_re[2] <= {{2{bf1_1_upper[9]}}, bf1_1_upper};
      s1_im[2] <= 12'sd0;
      s1_re[3] <= {{2{bf1_1_lower[9]}}, bf1_1_lower};
      s1_im[3] <= 12'sd0;
      s1_re[4] <= {{2{bf1_2_upper[9]}}, bf1_2_upper};
      s1_im[4] <= 12'sd0;
      s1_re[5] <= {{2{bf1_2_lower[9]}}, bf1_2_lower};
      s1_im[5] <= 12'sd0;
      s1_re[6] <= {{2{bf1_3_upper[9]}}, bf1_3_upper};
      s1_im[6] <= 12'sd0;
      s1_re[7] <= {{2{bf1_3_lower[9]}}, bf1_3_lower};
      s1_im[7] <= 12'sd0;
    end
  end

  // ============ STAGE 2: Butterflies on pairs separated by 2 ============
  // Twiddles: W8^0 = 1+0j, W8^2 = 0-1j (swap re/im, negate im)
  // bf(s1[0], s1[2]) * W8^0
  // bf(s1[1], s1[3]) * W8^2
  // bf(s1[4], s1[6]) * W8^0
  // bf(s1[5], s1[7]) * W8^2

  wire signed [12:0] bf2_0_re, bf2_0_im, bf2_0_re_tw, bf2_0_im_tw;
  wire signed [12:0] bf2_1_re, bf2_1_im, bf2_1_re_tw, bf2_1_im_tw;
  wire signed [12:0] bf2_2_re, bf2_2_im, bf2_2_re_tw, bf2_2_im_tw;
  wire signed [12:0] bf2_3_re, bf2_3_im, bf2_3_re_tw, bf2_3_im_tw;

  // Butterfly 0: W8^0 (1+0j) — just addition/subtraction
  assign bf2_0_re = {{1{s1_re[0][11]}}, s1_re[0]} + {{1{s1_re[2][11]}}, s1_re[2]};
  assign bf2_0_im = {{1{s1_im[0][11]}}, s1_im[0]} + {{1{s1_im[2][11]}}, s1_im[2]};
  assign bf2_0_re_tw = {{1{s1_re[0][11]}}, s1_re[0]} - {{1{s1_re[2][11]}}, s1_re[2]};
  assign bf2_0_im_tw = {{1{s1_im[0][11]}}, s1_im[0]} - {{1{s1_im[2][11]}}, s1_im[2]};

  // Butterfly 1: W8^2 = -j (swap, negate im)
  // lower_twiddle = (re - im*j) = re + (-im)*j
  assign bf2_1_re = {{1{s1_re[1][11]}}, s1_re[1]} + {{1{s1_im[3][11]}}, s1_im[3]};
  assign bf2_1_im = {{1{s1_im[1][11]}}, s1_im[1]} - {{1{s1_re[3][11]}}, s1_re[3]};
  assign bf2_1_re_tw = {{1{s1_re[1][11]}}, s1_re[1]} - {{1{s1_im[3][11]}}, s1_im[3]};
  assign bf2_1_im_tw = {{1{s1_im[1][11]}}, s1_im[1]} + {{1{s1_re[3][11]}}, s1_re[3]};

  // Butterfly 2: W8^0 (1+0j)
  assign bf2_2_re = {{1{s1_re[4][11]}}, s1_re[4]} + {{1{s1_re[6][11]}}, s1_re[6]};
  assign bf2_2_im = {{1{s1_im[4][11]}}, s1_im[4]} + {{1{s1_im[6][11]}}, s1_im[6]};
  assign bf2_2_re_tw = {{1{s1_re[4][11]}}, s1_re[4]} - {{1{s1_re[6][11]}}, s1_re[6]};
  assign bf2_2_im_tw = {{1{s1_im[4][11]}}, s1_im[4]} - {{1{s1_im[6][11]}}, s1_im[6]};

  // Butterfly 3: W8^2 = -j
  assign bf2_3_re = {{1{s1_re[5][11]}}, s1_re[5]} + {{1{s1_im[7][11]}}, s1_im[7]};
  assign bf2_3_im = {{1{s1_im[5][11]}}, s1_im[5]} - {{1{s1_re[7][11]}}, s1_re[7]};
  assign bf2_3_re_tw = {{1{s1_re[5][11]}}, s1_re[5]} - {{1{s1_im[7][11]}}, s1_im[7]};
  assign bf2_3_im_tw = {{1{s1_im[5][11]}}, s1_im[5]} + {{1{s1_re[7][11]}}, s1_re[7]};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid2 <= 1'b0;
      for (int i = 0; i < 8; i = i + 1) begin
        s2_re[i] <= 13'sd0;
        s2_im[i] <= 13'sd0;
      end
    end else begin
      valid2 <= valid1;
      s2_re[0] <= bf2_0_re;
      s2_im[0] <= bf2_0_im;
      s2_re[1] <= bf2_0_re_tw;
      s2_im[1] <= bf2_0_im_tw;
      s2_re[2] <= bf2_1_re;
      s2_im[2] <= bf2_1_im;
      s2_re[3] <= bf2_1_re_tw;
      s2_im[3] <= bf2_1_im_tw;
      s2_re[4] <= bf2_2_re;
      s2_im[4] <= bf2_2_im;
      s2_re[5] <= bf2_2_re_tw;
      s2_im[5] <= bf2_2_im_tw;
      s2_re[6] <= bf2_3_re;
      s2_im[6] <= bf2_3_im;
      s2_re[7] <= bf2_3_re_tw;
      s2_im[7] <= bf2_3_im_tw;
    end
  end

  // ============ STAGE 3: Butterflies on pairs separated by 4 ============
  // Twiddles: W8^0 = 1+0j, W8^1 = c-cj, W8^2 = -j, W8^3 = -c-cj
  // bf(s2[0], s2[4]) * W8^0
  // bf(s2[1], s2[5]) * W8^1
  // bf(s2[2], s2[6]) * W8^2
  // bf(s2[3], s2[7]) * W8^3

  // Complex multiply by Q1.7 twiddle: combinational logic
  // Input: (re_in, im_in) in Q format, (tw_re, tw_im) in Q1.7
  // Output: result shifted >> 7
  wire signed [20:0] bf3_1_re_prod, bf3_1_im_prod;
  wire signed [20:0] bf3_3_re_prod, bf3_3_im_prod;

  assign bf3_1_re_prod = s2_re[5] * $signed(C_Q17) - s2_im[5] * $signed(-C_Q17);
  assign bf3_1_im_prod = s2_re[5] * $signed(-C_Q17) + s2_im[5] * $signed(C_Q17);

  assign bf3_3_re_prod = s2_re[7] * $signed(-C_Q17) - s2_im[7] * $signed(-C_Q17);
  assign bf3_3_im_prod = s2_re[7] * $signed(-C_Q17) + s2_im[7] * $signed(-C_Q17);

  wire signed [12:0] bf3_0_re, bf3_0_im, bf3_0_re_tw, bf3_0_im_tw;
  wire signed [12:0] bf3_1_re, bf3_1_im, bf3_1_re_tw, bf3_1_im_tw;
  wire signed [12:0] bf3_2_re, bf3_2_im, bf3_2_re_tw, bf3_2_im_tw;
  wire signed [12:0] bf3_3_re, bf3_3_im, bf3_3_re_tw, bf3_3_im_tw;
  wire signed [20:0] bf3_1_re_shifted_wide, bf3_1_im_shifted_wide;
  wire signed [20:0] bf3_3_re_shifted_wide, bf3_3_im_shifted_wide;
  wire signed [12:0] bf3_1_re_shifted, bf3_1_im_shifted;
  wire signed [12:0] bf3_3_re_shifted, bf3_3_im_shifted;

  // Butterfly 0: W8^0 = 1+0j
  assign bf3_0_re = {{1{s2_re[0][12]}}, s2_re[0]} + {{1{s2_re[4][12]}}, s2_re[4]};
  assign bf3_0_im = {{1{s2_im[0][12]}}, s2_im[0]} + {{1{s2_im[4][12]}}, s2_im[4]};
  assign bf3_0_re_tw = {{1{s2_re[0][12]}}, s2_re[0]} - {{1{s2_re[4][12]}}, s2_re[4]};
  assign bf3_0_im_tw = {{1{s2_im[0][12]}}, s2_im[0]} - {{1{s2_im[4][12]}}, s2_im[4]};

  // Butterfly 1: W8^1 = c - cj where c = 91/128
  // Shift products by 7 and truncate
  assign bf3_1_re_shifted_wide = bf3_1_re_prod >>> 7;
  assign bf3_1_im_shifted_wide = bf3_1_im_prod >>> 7;
  assign bf3_1_re_shifted = bf3_1_re_shifted_wide[12:0];
  assign bf3_1_im_shifted = bf3_1_im_shifted_wide[12:0];
  assign bf3_1_re = {{1{s2_re[1][12]}}, s2_re[1]} + bf3_1_re_shifted;
  assign bf3_1_im = {{1{s2_im[1][12]}}, s2_im[1]} + bf3_1_im_shifted;
  assign bf3_1_re_tw = {{1{s2_re[1][12]}}, s2_re[1]} - bf3_1_re_shifted;
  assign bf3_1_im_tw = {{1{s2_im[1][12]}}, s2_im[1]} - bf3_1_im_shifted;

  // Butterfly 2: W8^2 = -j
  assign bf3_2_re = {{1{s2_re[2][12]}}, s2_re[2]} + {{1{s2_im[6][12]}}, s2_im[6]};
  assign bf3_2_im = {{1{s2_im[2][12]}}, s2_im[2]} - {{1{s2_re[6][12]}}, s2_re[6]};
  assign bf3_2_re_tw = {{1{s2_re[2][12]}}, s2_re[2]} - {{1{s2_im[6][12]}}, s2_im[6]};
  assign bf3_2_im_tw = {{1{s2_im[2][12]}}, s2_im[2]} + {{1{s2_re[6][12]}}, s2_re[6]};

  // Butterfly 3: W8^3 = -c - cj
  assign bf3_3_re_shifted_wide = bf3_3_re_prod >>> 7;
  assign bf3_3_im_shifted_wide = bf3_3_im_prod >>> 7;
  assign bf3_3_re_shifted = bf3_3_re_shifted_wide[12:0];
  assign bf3_3_im_shifted = bf3_3_im_shifted_wide[12:0];
  assign bf3_3_re = {{1{s2_re[3][12]}}, s2_re[3]} + bf3_3_re_shifted;
  assign bf3_3_im = {{1{s2_im[3][12]}}, s2_im[3]} + bf3_3_im_shifted;
  assign bf3_3_re_tw = {{1{s2_re[3][12]}}, s2_re[3]} - bf3_3_re_shifted;
  assign bf3_3_im_tw = {{1{s2_im[3][12]}}, s2_im[3]} - bf3_3_im_shifted;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid3 <= 1'b0;
      for (int i = 0; i < 8; i = i + 1) begin
        s3_re[i] <= 14'sd0;
        s3_im[i] <= 14'sd0;
      end
    end else begin
      valid3 <= valid2;
      s3_re[0] <= {{1{bf3_0_re[12]}}, bf3_0_re};
      s3_im[0] <= {{1{bf3_0_im[12]}}, bf3_0_im};
      s3_re[1] <= {{1{bf3_1_re[12]}}, bf3_1_re};
      s3_im[1] <= {{1{bf3_1_im[12]}}, bf3_1_im};
      s3_re[2] <= {{1{bf3_2_re[12]}}, bf3_2_re};
      s3_im[2] <= {{1{bf3_2_im[12]}}, bf3_2_im};
      s3_re[3] <= {{1{bf3_3_re[12]}}, bf3_3_re};
      s3_im[3] <= {{1{bf3_3_im[12]}}, bf3_3_im};
      s3_re[4] <= {{1{bf3_0_re_tw[12]}}, bf3_0_re_tw};
      s3_im[4] <= {{1{bf3_0_im_tw[12]}}, bf3_0_im_tw};
      s3_re[5] <= {{1{bf3_1_re_tw[12]}}, bf3_1_re_tw};
      s3_im[5] <= {{1{bf3_1_im_tw[12]}}, bf3_1_im_tw};
      s3_re[6] <= {{1{bf3_2_re_tw[12]}}, bf3_2_re_tw};
      s3_im[6] <= {{1{bf3_2_im_tw[12]}}, bf3_2_im_tw};
      s3_re[7] <= {{1{bf3_3_re_tw[12]}}, bf3_3_re_tw};
      s3_im[7] <= {{1{bf3_3_im_tw[12]}}, bf3_3_im_tw};
    end
  end

  // ============ Bit-reverse the outputs back to natural order ============
  // S3 outputs are in FFT order for bit-reversed input; map back to natural order
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_valid <= 1'b0;
      X0_re <= 16'sd0;  X0_im <= 16'sd0;
      X1_re <= 16'sd0;  X1_im <= 16'sd0;
      X2_re <= 16'sd0;  X2_im <= 16'sd0;
      X3_re <= 16'sd0;  X3_im <= 16'sd0;
      X4_re <= 16'sd0;  X4_im <= 16'sd0;
      X5_re <= 16'sd0;  X5_im <= 16'sd0;
      X6_re <= 16'sd0;  X6_im <= 16'sd0;
      X7_re <= 16'sd0;  X7_im <= 16'sd0;
    end else begin
      out_valid <= valid3;
      // Direct natural order: X[k] = s3[k] (already in natural order from DIT processing)
      X0_re <= {{2{s3_re[0][13]}}, s3_re[0]};
      X0_im <= {{2{s3_im[0][13]}}, s3_im[0]};
      X1_re <= {{2{s3_re[1][13]}}, s3_re[1]};
      X1_im <= {{2{s3_im[1][13]}}, s3_im[1]};
      X2_re <= {{2{s3_re[2][13]}}, s3_re[2]};
      X2_im <= {{2{s3_im[2][13]}}, s3_im[2]};
      X3_re <= {{2{s3_re[3][13]}}, s3_re[3]};
      X3_im <= {{2{s3_im[3][13]}}, s3_im[3]};
      X4_re <= {{2{s3_re[4][13]}}, s3_re[4]};
      X4_im <= {{2{s3_im[4][13]}}, s3_im[4]};
      X5_re <= {{2{s3_re[5][13]}}, s3_re[5]};
      X5_im <= {{2{s3_im[5][13]}}, s3_im[5]};
      X6_re <= {{2{s3_re[6][13]}}, s3_re[6]};
      X6_im <= {{2{s3_im[6][13]}}, s3_im[6]};
      X7_re <= {{2{s3_re[7][13]}}, s3_re[7]};
      X7_im <= {{2{s3_im[7][13]}}, s3_im[7]};
    end
  end

endmodule
