/* Il nucleo compilato di SinapsiDB: i percorsi che passano molti fatti, sulle colonne del motore così come sono.
 *
 * Non conosce Python: riceve puntatori alle colonne (interi a 32 bit) e a due tabelle preparate da chi chiama, e
 * restituisce uno stato. Non alloca memoria, non scrive fuori dai vettori che riceve, non tiene niente tra una
 * chiamata e l'altra. Se trova qualcosa che non sa trattare si ferma e lo dice: chi chiama rifà il conto in Python,
 * che resta il riferimento. Non usa la libreria C. Si compila con: python -m sinapsidb.nucleo compila
 */
#include <stdint.h>

#ifndef SINAPSI_SORGENTE
#define SINAPSI_SORGENTE "?"
#endif

/* L'impronta del sorgente da cui la libreria è stata compilata: chi la carica controlla che sia quella del suo. */
const char *sinapsi_nucleo_sorgente(void) { return SINAPSI_SORGENTE; }

/* Gli stati che una funzione può restituire. */
enum {
    FATTO = 0,
    MANCA_UN_NUMERO = 1,    /* la tabella dei numeri non ha ancora la voce di un neurone: in *chi c'è quale */
    MANCA_UN_GRUPPO = 2,    /* la tabella dei gruppi non ha il posto di un neurone: in *chi c'è quale */
    TROPPO_GRANDE = 3,      /* una somma non sta in 64 bit */
    NON_SOMMABILE = 4,      /* un numero che i milionesimi non rappresentano senza perdere niente */
    COLONNE_INCOERENTI = 5  /* un fatto punta fuori dalle sinapsi: non si legge niente */
};

/* Che cosa dice la tabella dei numeri di un neurone, oltre al numero: quanti decimali ha (da 0 a 6), oppure: */
#define SCALA_IGNOTA 255 /* non è ancora stato guardato */
#define NON_NUMERO 254   /* il suo nome non è un numero: nelle somme si salta */
#define FUORI_MISURA 253 /* è un numero, ma non sta nei milionesimi a 64 bit */

/* Passa tutti i fatti in vigore di un tipo e, gruppo per gruppo, li conta oppure somma il valore di un ruolo.
 *
 * Le colonne: f_tipo, f_morto (-1 se il fatto è in vigore), f_inizio (la prima sinapsi del fatto), s_a (il neurone
 * di ogni sinapsi). posto_gruppo e posto_valore sono le posizioni dei due ruoli dentro il fatto: valgono per tutti i
 * fatti del tipo, e lo garantisce chi chiama. posto_gruppo < 0: un gruppo solo. posto_valore < 0: si conta soltanto.
 *
 * unita e scala, lunghe n_numeri: per ogni neurone il numero in milionesimi e quanti decimali ha.
 * indice_di, lunga n_indici: per ogni neurone il posto del suo gruppo, da 0 a n_gruppi - 1, oppure -1.
 *
 * In uscita, per ogni gruppo: somme (in milionesimi), scale (il massimo dei decimali degli addendi), quanti (gli
 * addendi, oppure i fatti se si conta). Chi chiama li passa azzerati. *visti: i fatti in vigore del tipo.
 * Se lo stato non è FATTO quello che c'è in uscita non vale niente.
 */
int32_t sinapsi_aggrega(int64_t n_fatti, const int32_t *f_tipo, const int32_t *f_morto, const int32_t *f_inizio,
                        int64_t n_sinapsi, const int32_t *s_a, int32_t tipo, int32_t posto_gruppo, int32_t posto_valore,
                        int64_t n_numeri, const int64_t *unita, const uint8_t *scala,
                        int64_t n_indici, const int32_t *indice_di,
                        int64_t n_gruppi, int64_t *somme, uint8_t *scale, int64_t *quanti, int64_t *visti, int32_t *chi) {
    int64_t contati = 0;
    int32_t ultimo = posto_gruppo > posto_valore ? posto_gruppo : posto_valore;
    *visti = 0;
    *chi = -1;
    if (n_gruppi < 1) return COLONNE_INCOERENTI;
    for (int64_t f = 0; f < n_fatti; f++) {
        if (f_tipo[f] != tipo || f_morto[f] >= 0) continue;
        int64_t base = f_inizio[f];
        if (base < 0 || (ultimo >= 0 && base + ultimo >= n_sinapsi)) return COLONNE_INCOERENTI;
        contati++;
        int64_t g = 0;
        if (posto_gruppo >= 0) {
            int32_t n = s_a[base + posto_gruppo];
            if (n < 0 || n >= n_indici || (g = indice_di[n]) < 0 || g >= n_gruppi) {
                *chi = n;
                return MANCA_UN_GRUPPO;
            }
        }
        if (posto_valore < 0) {
            quanti[g]++;
            continue;
        }
        int32_t v = s_a[base + posto_valore];
        if (v < 0 || v >= n_numeri || scala[v] == SCALA_IGNOTA) {
            *chi = v;
            return MANCA_UN_NUMERO;
        }
        uint8_t decimali = scala[v];
        if (decimali == NON_NUMERO) continue;
        if (decimali == FUORI_MISURA) return NON_SOMMABILE;
        if (__builtin_add_overflow(somme[g], unita[v], &somme[g])) return TROPPO_GRANDE;
        if (decimali > scale[g]) scale[g] = decimali;
        quanti[g]++;
    }
    *visti = contati;
    return FATTO;
}
