/*
 * ea_edge_sampler — captures din on rising edges of clk.
 *
 * Two doubles of state: [0] is the last committed Q, [1] is the prior
 * clk value used for edge detection. A rising edge is the transition
 * of clk across clk_threshold; on that event Q takes din. delay_s
 * lets the user model propagation delay — it enters via ngspice's
 * OUTPUT_DELAY hook.
 *
 * Original code written for eda-agents.
 */

void ucm_ea_edge_sampler(ARGS)
{
    double threshold = PARAM(clk_threshold);
    double delay     = PARAM(delay_s);
    if (delay < 0.0) {
        delay = 0.0;
    }

    double din = INPUT(din);
    double clk = INPUT(clk);

    double *q_state;
    double *clk_prev;

    if (INIT) {
        cm_analog_alloc(0, 2 * (int) sizeof(double));
        q_state = (double *) cm_analog_get_ptr(0, 0);
        clk_prev = q_state + 1;
        *q_state = 0.0;
        *clk_prev = clk;
    } else {
        double *prior = (double *) cm_analog_get_ptr(0, 1);
        q_state = (double *) cm_analog_get_ptr(0, 0);
        clk_prev = q_state + 1;
        *q_state = prior[0];
        *clk_prev = prior[1];
    }

    /* Rising edge: previous clk below threshold, current clk at/above. */
    if ((*clk_prev < threshold) && (clk >= threshold)) {
        *q_state = din;
    }
    *clk_prev = clk;

    OUTPUT(q) = *q_state;
    if (ANALYSIS != DC && delay > 0.0) {
        OUTPUT_DELAY(q) = delay;
    }
    PARTIAL(q, din) = 0.0;
    PARTIAL(q, clk) = 0.0;
}
