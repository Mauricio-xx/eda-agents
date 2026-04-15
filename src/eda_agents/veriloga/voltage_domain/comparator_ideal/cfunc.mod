/*
 * ea_comparator_ideal — ideal voltage comparator
 *
 * Emits vout_high when inp > inn + hyst/2 and vout_low when
 * inp < inn - hyst/2; otherwise holds the previous output. One
 * double of per-instance state stores the last committed output.
 *
 * Original code written for eda-agents; not derived from any
 * Arcadia-1 veriloga-skills / EVAS source.
 */

void ucm_ea_comparator_ideal(ARGS)
{
    double vout_high = PARAM(vout_high);
    double vout_low  = PARAM(vout_low);
    double hyst      = PARAM(hysteresis_v);
    if (hyst < 0.0) {
        hyst = 0.0;
    }

    double vp = INPUT(inp);
    double vn = INPUT(inn);
    double diff = vp - vn;

    double *prev;
    if (INIT) {
        cm_analog_alloc(0, (int) sizeof(double));
        prev = (double *) cm_analog_get_ptr(0, 0);
        /* Initialise to the midpoint so the first step evaluates the
         * full diff against the band instead of reusing a noise value. */
        *prev = 0.5 * (vout_high + vout_low);
    } else {
        double *prior = (double *) cm_analog_get_ptr(0, 1);
        prev = (double *) cm_analog_get_ptr(0, 0);
        *prev = *prior;
    }

    double out;
    double half = 0.5 * hyst;
    if (diff > half) {
        out = vout_high;
    } else if (diff < -half) {
        out = vout_low;
    } else {
        out = *prev;
    }
    *prev = out;

    OUTPUT(out) = out;
    /* Partials: the ideal comparator is piecewise constant so the
     * only non-zero derivative sits exactly on the decision line,
     * which has measure zero in time — reporting 0 keeps ngspice
     * Newton iterations stable. */
    PARTIAL(out, inp) = 0.0;
    PARTIAL(out, inn) = 0.0;
}
