-- Database PRIVATO della prova. Tutte le relazioni sono LOGGED. Nessuna estensione.
CREATE SCHEMA prova_fido;
SET LOCAL search_path TO prova_fido, pg_catalog;

-- 0 e 1 sono la definizione della formula e della regola fissa del carico.
CREATE TABLE definizioni (evento bigint PRIMARY KEY, nome text NOT NULL);
INSERT INTO definizioni VALUES (0,'fido residuo'), (1,'fido non superato');
CREATE TABLE testa (
    unica boolean PRIMARY KEY CHECK (unica), ultimo bigint NOT NULL,
    scritto float8 NOT NULL, coda_cache boolean NOT NULL DEFAULT false,
    formato smallint NOT NULL DEFAULT 1, dal bigint NOT NULL DEFAULT 2
);
INSERT INTO testa(unica,ultimo,scritto) VALUES (true,1,0);
CREATE TYPE apporto AS (
    cliente text, valore numeric, da float8, fine float8, esplicito boolean, specie text, riferimento text
);
CREATE TABLE eventi (
    evento bigint PRIMARY KEY, chiave text UNIQUE NOT NULL, richiesta jsonb NOT NULL,
    arrivo float8 NOT NULL, scritto float8 NOT NULL CHECK (scritto >= arrivo),
    nuovo apporto, ritira bigint, esito text NOT NULL CHECK (esito IN ('applicato','violazione_regola')),
    letture jsonb NOT NULL
);
CREATE TABLE fatti (
    nato bigint PRIMARY KEY REFERENCES eventi, morto bigint REFERENCES eventi,
    cliente text NOT NULL, valore numeric NOT NULL, da float8 NOT NULL, fine float8 NOT NULL CHECK (da < fine),
    esplicito boolean NOT NULL, specie text NOT NULL, riferimento text NOT NULL
);
CREATE INDEX fatti_storia ON fatti(cliente, nato);
CREATE INDEX fatti_attivi ON fatti(cliente, da, fine) INCLUDE (valore) WHERE morto IS NULL;
CREATE INDEX fatti_ritirati ON fatti(morto) WHERE morto IS NOT NULL;
CREATE INDEX pagamenti_vivi ON fatti(riferimento) WHERE specie = 'pagamento' AND morto IS NULL;
CREATE TABLE clienti (
    cliente text PRIMARY KEY, saldo numeric NOT NULL DEFAULT 0, revisione bigint NOT NULL DEFAULT 1,
    da float8 NOT NULL DEFAULT '-Infinity', fine float8 NOT NULL DEFAULT 'Infinity'
);
CREATE TABLE dipendenze (
    cliente text NOT NULL, al float8 NOT NULL, evento bigint NOT NULL REFERENCES eventi,
    PRIMARY KEY(cliente, al, evento)
);
-- Riempita solo nello schieramento +Redis. Un consumatore può ripartire dal suo evento.
CREATE TABLE coda_cache (
    evento bigint NOT NULL REFERENCES eventi, cliente text NOT NULL REFERENCES clienti,
    PRIMARY KEY(evento, cliente)
);

CREATE FUNCTION fotografia(c text, t float8)
RETURNS TABLE (saldo numeric, da float8, fine float8) LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(f.valore) FILTER (WHERE f.da <= t AND t < f.fine), 0),
           greatest('-Infinity'::float8, max(f.da) FILTER (WHERE f.da <= t), max(f.fine) FILTER (WHERE f.fine <= t)),
           least('Infinity'::float8, min(f.da) FILTER (WHERE f.da > t), min(f.fine) FILTER (WHERE f.fine > t))
      FROM prova_fido.fatti f WHERE f.cliente=c AND f.morto IS NULL
$$;

-- NULL per la base significa la transazione corrente. Una lettura storica non
-- usa il saldo mantenuto: il prefisso e i soli eventi scelti definiscono lo scenario.
CREATE FUNCTION saldo_al(c text, t float8, base bigint, scelte bigint[]) RETURNS numeric
LANGUAGE plpgsql STABLE SET search_path TO prova_fido, pg_catalog AS $$
DECLARE x numeric;
BEGIN
    IF base IS NULL THEN
        SELECT saldo INTO x FROM clienti WHERE cliente=c AND da<=t AND t<fine;
        IF FOUND THEN RETURN x; END IF;
        RETURN (SELECT saldo FROM fotografia(c,t));
    END IF;
    RETURN (SELECT coalesce(sum(valore), 0) FROM fatti
             WHERE cliente=c AND (nato<=base OR nato=ANY(scelte))
               AND (morto IS NULL OR (morto>base AND NOT morto=ANY(scelte)))
               AND da<=t AND t<fine AND (esplicito OR nato<=base OR da<t));
END $$;

-- La stessa regola su arrivo/commit, scrittura reale, verifica e riesame.
CREATE FUNCTION valuta(base bigint, scelte bigint[], arrivo float8, scritto float8,
                       nuovo apporto, ritira bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path TO prova_fido, pg_catalog AS $$
DECLARE t float8; d record; prima numeric; dopo numeric; vieta boolean;
        letture jsonb := '[]'; giro integer; comune jsonb;
BEGIN
    FOR giro IN 1..(CASE WHEN arrivo=scritto THEN 1 ELSE 2 END) LOOP
        t := CASE WHEN giro=1 THEN arrivo ELSE scritto END;
        vieta := false;
        FOR d IN
            SELECT cliente, sum(v) AS delta FROM (
                SELECT nuovo.cliente, nuovo.valore AS v
                 WHERE nuovo.cliente IS NOT NULL AND (NOT nuovo.esplicito OR nuovo.da<=t) AND t<nuovo.fine
                UNION ALL
                SELECT f.cliente,-f.valore FROM fatti f WHERE f.nato=ritira AND f.da<=t AND t<f.fine
            ) a GROUP BY cliente ORDER BY cliente
        LOOP
            prima := saldo_al(d.cliente,t,base,scelte);
            dopo := prima+d.delta;
            vieta := vieta OR dopo<0;
            comune := jsonb_build_object('quando',CASE WHEN giro=1 THEN 'arrivo' ELSE 'scrittura' END,
                'regola','fido non superato','versione',1,'su',d.cliente,'regola_id',1,'su_id',d.cliente,
                'verdetto',CASE WHEN dopo<0 THEN 'vieta' ELSE 'passa' END,'segnale','residuo negativo',
                'cosa','fido residuo','cosa_id',0,'di',d.cliente,'valido_al',t);
            letture := letture || jsonb_build_array(
                comune || jsonb_build_object('con_proposta',false,'valore',trim_scale(prima)::text),
                comune || jsonb_build_object('con_proposta',true,'valore',trim_scale(dopo)::text));
        END LOOP;
        IF vieta THEN EXIT; END IF;
    END LOOP;
    RETURN jsonb_build_object('esito',CASE WHEN vieta THEN 'violazione_regola' ELSE 'applicato' END,'letture',letture);
END $$;

CREATE FUNCTION ricevuta(e eventi) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_object('evento',e.evento,'base',e.evento-1,'arrivo',e.arrivo,'scritto',e.scritto,'esito',e.esito)
$$;

CREATE FUNCTION scrivi(q jsonb, t_arrivo float8) RETURNS jsonb
LANGUAGE plpgsql SET search_path TO prova_fido, pg_catalog AS $$
DECLARE op text := q->>'op'; k text := q->>'chiave'; c text := q->>'cliente'; n apporto;
        vecchio fatti; corrente eventi; esame jsonb; t float8; e bigint; testa_prima testa;
        cli clienti; fr record; aggiunta numeric; rimozione numeric; v numeric;
BEGIN
    -- Prima la chiave, poi la causale, poi le righe dei clienti in ordine deterministico.
    -- La stessa chiave ritentata durante un commit non viene decisa due volte.
    PERFORM pg_advisory_xact_lock(4910, hashtext(k));
    SELECT * INTO corrente FROM eventi WHERE chiave=k;
    IF FOUND THEN
        IF corrente.richiesta<>q THEN RETURN '{"esito":"conflitto","ripetuta":false}'::jsonb; END IF;
        RETURN ricevuta(corrente) || '{"ripetuta":true}'::jsonb;
    END IF;
    IF op IN ('storno','sposta','pagamento') THEN
        PERFORM pg_advisory_xact_lock(4911, hashtext(q->>'causale'));
        SELECT * INTO vecchio FROM fatti
         WHERE riferimento=q->>'causale' AND specie='pagamento' AND morto IS NULL;
        IF op='pagamento' AND FOUND THEN
            RAISE EXCEPTION 'causale già presente nel carico' USING ERRCODE='22023';
        ELSIF op IN ('storno','sposta') AND NOT FOUND THEN
            RAISE EXCEPTION 'pagamento da rettificare non presente' USING ERRCODE='22023';
        END IF;
    END IF;
    INSERT INTO clienti(cliente) SELECT DISTINCT x FROM unnest(ARRAY[c,vecchio.cliente]) x
        WHERE x IS NOT NULL ORDER BY x ON CONFLICT DO NOTHING;
    PERFORM 1 FROM clienti WHERE cliente IN (c,vecchio.cliente) ORDER BY cliente FOR UPDATE;

    -- Numero transazionale; il blocco rimane fino al commit e un rollback non lascia buchi.
    SELECT * INTO STRICT testa_prima FROM testa WHERE unica FOR UPDATE;
    e := testa_prima.ultimo+1;
    t := greatest(t_arrivo, extract(epoch FROM clock_timestamp())::float8, testa_prima.scritto);
    IF op<>'storno' THEN
        v := (q->>'importo')::numeric;
        n := ROW(c, CASE WHEN op='ordine' THEN -v ELSE v END,
                 coalesce(extract(epoch FROM (q->>'vale_da')::timestamptz)::float8,t),
                 coalesce(extract(epoch FROM (q->>'vale_fino_a')::timestamptz)::float8,'Infinity'::float8),
                 q ? 'vale_da', CASE WHEN op='sposta' THEN 'pagamento' ELSE op END,
                 coalesce(q->>'atto',q->>'numero',q->>'causale'))::apporto;
        IF n.da>=n.fine THEN RAISE EXCEPTION 'periodo vuoto al commit' USING ERRCODE='22023'; END IF;
    END IF;
    esame := valuta(NULL,'{}',t_arrivo,t,n,vecchio.nato);
    INSERT INTO eventi VALUES (e,k,q,t_arrivo,t,n,vecchio.nato,esame->>'esito',esame->'letture') RETURNING * INTO corrente;
    INSERT INTO dipendenze SELECT DISTINCT y->>'su',(y->>'valido_al')::float8,e FROM jsonb_array_elements(corrente.letture) y;
    IF corrente.esito='applicato' THEN
        IF vecchio.nato IS NOT NULL THEN UPDATE fatti SET morto=e WHERE nato=vecchio.nato; END IF;
        IF n.cliente IS NOT NULL THEN
            INSERT INTO fatti VALUES (e,NULL,n.cliente,n.valore,n.da,n.fine,n.esplicito,n.specie,n.riferimento);
        END IF;
        FOR cli IN SELECT * FROM clienti WHERE cliente IN (c,vecchio.cliente) LOOP
            IF cli.da<=t AND t<cli.fine THEN
                aggiunta := CASE WHEN n.cliente=cli.cliente AND n.da<=t AND t<n.fine THEN n.valore ELSE 0 END;
                rimozione := CASE WHEN vecchio.cliente=cli.cliente AND vecchio.da<=t AND t<vecchio.fine THEN vecchio.valore ELSE 0 END;
                cli.saldo := cli.saldo+aggiunta-rimozione;
                IF n.cliente=cli.cliente THEN
                    cli.da := greatest(cli.da, CASE WHEN n.da<=t THEN n.da END, CASE WHEN n.fine<=t THEN n.fine END);
                    cli.fine := least(cli.fine, CASE WHEN n.da>t THEN n.da END, CASE WHEN n.fine>t THEN n.fine END);
                END IF;
            ELSE
                SELECT * INTO fr FROM fotografia(cli.cliente,t);
                cli.saldo := fr.saldo; cli.da := fr.da; cli.fine := fr.fine;
            END IF;
            UPDATE clienti SET saldo=cli.saldo, da=cli.da, fine=cli.fine, revisione=e WHERE cliente=cli.cliente;
            IF testa_prima.coda_cache THEN INSERT INTO coda_cache VALUES (e,cli.cliente); END IF;
        END LOOP;
    END IF;
    UPDATE testa SET ultimo=e, scritto=t WHERE unica;
    RETURN ricevuta(corrente) || '{"ripetuta":false}'::jsonb;
END $$;

-- Un unico snapshot per la lettura calda. Fuori finestra si aggiorna il saldo
-- sotto lo stesso lock per-cliente usato dalle scritture (nessun nuovo evento).
CREATE FUNCTION proiezione(c text, t float8) RETURNS jsonb
LANGUAGE plpgsql SET search_path TO prova_fido, pg_catalog AS $$
DECLARE cli clienti; e bigint; fr record;
BEGIN
    SELECT cl AS dati, g.ultimo INTO fr FROM testa g LEFT JOIN clienti cl ON cl.cliente=c;
    cli := fr.dati; e := fr.ultimo;
    IF cli.cliente IS NULL THEN
        RETURN jsonb_build_object('valore','0','revisione',1,'formula',0,'da',NULL,'fine',NULL,'aggiornato_a',e,'valido_al',t);
    END IF;
    IF NOT (cli.da<=t AND t<cli.fine) THEN
        SELECT * INTO cli FROM clienti WHERE cliente=c FOR UPDATE;
        SELECT ultimo INTO e FROM testa WHERE unica;
        IF NOT (cli.da<=t AND t<cli.fine) THEN
            SELECT * INTO fr FROM fotografia(c,t);
            cli.saldo := fr.saldo; cli.da := fr.da; cli.fine := fr.fine;
            UPDATE clienti SET saldo=cli.saldo,da=cli.da,fine=cli.fine WHERE cliente=c;
        END IF;
    END IF;
    RETURN jsonb_build_object('valore',trim_scale(cli.saldo)::text,'revisione',cli.revisione,'formula',0,
             'da',nullif(cli.da,'-Infinity'::float8),'fine',nullif(cli.fine,'Infinity'::float8),
             'aggiornato_a',e,'valido_al',t);
END $$;
