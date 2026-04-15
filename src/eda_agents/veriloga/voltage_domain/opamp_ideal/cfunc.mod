/*
 * ea_opamp_ideal — single-pole op-amp, behavioural.
 *
 * Continuous-time first-order low-pass of A0*(inp-inn) with pole
 * wp = 2*pi*fp_hz. Output saturates at [vmin, vmax]. The integrator
 * state lives in per-instance analog storage so it survives Newton
 * iteration retries.
 *
 * Original code written for eda-agents.
 */

#include <math.h>

void ucm_ea_opamp_ideal(ARGS)
{
    double a0   = PARAM(a0);
    double fp   = PARAM(fp_hz);
    double vmax = PARAM(vmax);
    double vmin = PARAM(vmin);

    if (fp <= 0.0) {
        fp = 1.0;
    }
    double wp = 6.283185307179586 * fp;

    double diff = INPUT(inp) - INPUT(inn);
    double vin = a0 * diff;

    double *state;
    if (INIT) {
        cm_analog_alloc(0, (int) sizeof(double));
        state = (double *) cm_analog_get_ptr(0, 0);
        *state = 0.0;
    } else {
        state = (double *) cm_analog_get_ptr(0, 0);
    }

    double vout;
    if (ANALYSIS == DC) {
        vout = vin;
    } else {
        /* dV/dt = wp*(vin - V). Trapezoidal integration via
         * cm_analog_integrate keeps the pole stable across variable
         * time-steps. */
        double integrand = wp * (vin - *state);
        double partial = 0.0;
        double integral = *state;
        cm_analog_integrate(integrand, &integral, &partial);
        *state = integral;
        vout = integral;
    }

    if (vout > vmax) vout = vmax;
    if (vout < vmin) vout = vmin;

    OUTPUT(out) = vout;
    /* Small-signal partials around the linear region. */
    double gain;
    if (vout >= vmax || vout <= vmin) {
        gain = 0.0;
    } else if (ANALYSIS == DC) {
        gain = a0;
    } else {
        /* magnitude at operating point is dominated by wp filter */
        gain = a0;
    }
    PARTIAL(out, inp) =  gain;
    PARTIAL(out, inn) = -gain;
}
