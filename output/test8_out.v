module top(a, in0, b, out3, y);

input a, b, in0;
output out3, y;
wire _gc_ctrl, n1, n2, n_in0_n;

and U1(n1, a, b);
and U_gc__buf0(y, n1, _gc_ctrl);
not U2(n_in0_n, in0);
buf U3(out3, n_in0_n);

endmodule
