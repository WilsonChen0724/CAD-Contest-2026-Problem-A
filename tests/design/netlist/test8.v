module top(a, b, in0, out3, y);

input a, b, in0;
output out3, y;
wire n1, n2, n_in0_n;

and U1(n1, a, b);
buf U_gc__buf0(y, n1);
not U2(n_in0_n, in0);
buf U3(out3, n_in0_n);

endmodule
