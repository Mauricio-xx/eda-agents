/*
 * ea_clock_gen — free-running voltage clock source.
 *
 * Emits v_high for duty*period seconds, v_low for the remainder.
 * delay_s shifts the leading edge to let transient analyses settle
 * before clock activity begins.
 *
 * Original code written for eda-agents.
 */

#include <math.h>

void ucm_ea_clock_gen(ARGS)
{
    double period = PARAM(period_s);
    double duty   = PARAM(duty);
    double v_high = PARAM(v_high);
    double v_low  = PARAM(v_low);
    double delay  = PARAM(delay_s);

    if (period <= 0.0) {
        period = 1.0e-9;
    }
    if (duty < 0.0) duty = 0.0;
    if (duty > 1.0) duty = 1.0;

    double t = TIME - delay;
    double level;

    if (ANALYSIS == DC || t < 0.0) {
        level = v_low;
    } else {
        double phase = fmod(t, period);
        level = (phase < duty * period) ? v_high : v_low;

        /* Schedule breakpoints at each edge so ngspice does not stride
         * over the transition and miss a toggle. */
        double cycle_start = t - phase + delay;
        double next_edge = cycle_start + duty * period;
        if (next_edge <= TIME) {
            next_edge = cycle_start + period;
        }
        cm_analog_set_temp_bkpt(next_edge);
    }

    OUTPUT(out) = level;
    /* Free-running source has no input ports, so no PARTIAL entries. */
}
