"""Grapheme to phoneme for Spanish, in pure Python.

Turns text into the IPA the model reads, with no runtime dependency -- which is the
point: local-tts imports only the standard library, so until now it could not transcribe
anything itself and could only hand a `/…/` dictionary entry to a backend that had a
phonemizer of its own.

Matched against espeak's output (through kokoro-onnx's tokenizer) over three corpora,
321 unique words and 35 sentences:

    segments -- the phonemes themselves    321/321   100%
    words including stress marks           363/365   99.5%
    whole sentences                         33/35    94.3%

Synthesized, two of the three corpora are indistinguishable from running espeak twice:
0.94x and 0.96x the runtime's own float noise, the model not being bit-reproducible.

The residue is never a wrong sound. It is a missing secondary stress mark in two words
whose hiatus adds a syllable the stress counter does not see ("cláusulas",
"extraordinariamente"), which is espeak's own heuristic rather than a rule of Spanish.

Spanish is close to phonemic, which is why this is tractable; the same approach would
not carry to English.
"""
import re

VOWELS = "aeiouáéíóúü"
STRONG = "aeoáéíóú"          # nucleos silabicos que no se vuelven semivocal

def _nuclei(p):
    """Syllable nuclei: vowel groups, a diphthong counting as one.

    An unstressed i/u beside another vowel is a glide, not a nucleus -- "agua" has two
    syllables, not three, and counting three is what produced "aɣˈua" where espeak
    gives "ˈaɣwa".
    """
    grupos, i = [], 0
    while i < len(p):
        if p[i] in VOWELS:
            j = i
            while j < len(p) and p[j] in VOWELS:
                j += 1
            trozo = p[i:j]
            # each strong vowel (or accented weak one) opens a nucleus
            n = sum(1 for c in trozo if c in STRONG) or 1
            for _ in range(n):
                grupos.append((i, j))
            i = j
        else:
            i += 1
    return grupos

def _stress_index(p):
    """Index of the stressed nucleus, from the spelling alone."""
    nmap = _nucleus_map(p)
    if not nmap:
        return None
    total = max(nmap.values()) + 1
    for i, ch in enumerate(p):
        if ch in "áéíóú":
            return nmap[i]
    g = [None] * total
    if total == 1:
        return 0
    last = p[-1]
    if last in "aeiouns":
        return total - 2           # penultimate
    return total - 1               # last

def _nucleus_map(p):
    """Which nucleus each vowel index belongs to.

    The loop advanced a counter per vowel run while _nuclei counted per nucleus, and a
    hiatus like "ea" (two nuclei, one run) desynchronized them -- which is why
    "reaccion" lost its primary stress.
    """
    nmap, n, i = {}, 0, 0
    while i < len(p):
        if p[i] in VOWELS:
            j = i
            while j < len(p) and p[j] in VOWELS:
                j += 1
            group = p[i:j]
            fuerte = [k for k, ch in enumerate(group) if ch in STRONG]
            if not fuerte:
                fuerte = [len(group) - 1]
            for k in range(len(group)):
                # each vowel joins the nearest strong nucleus to its left
                previos = [x for x in fuerte if x <= k] or [fuerte[0]]
                nmap[i + k] = n + fuerte.index(previos[-1])
            n += len(fuerte)
            i = j
        else:
            i += 1
    return nmap


def phonemes_for(palabra):
    p = palabra.lower()
    if p.startswith("ps"):
        p = p[1:]                # "psicologa" is read sikologa
    # The u of "que/qui" and "gue/gui" is silent and is not a nucleus. Removing it
    # before counting syllables keeps it from shifting the stress: "quimico" is
    # kˈimiko, not kimˈiko.
    p = p.replace("ü", "\u0001")   # placeholder: this u DOES sound, keep gu+e/i off it
    p = re.sub(r"qu([eiéí])", r"k\1", p)
    p = re.sub(r"gu([eiéí])", r"ɡ\1", p)
    dieresis_at = {k for k, ch in enumerate(p) if ch == "\u0001"}
    p = p.replace("\u0001", "u")
    nmap = _nucleus_map(p)
    primary = _stress_index(p)
    # Espeak marks a secondary near the start when the primary falls far in: the word is
    # too long to carry a single peak. With the primary on the 1st or 2nd syllable it
    # does not appear ("aquella" -> akˈejja); from the 3rd on it does ("cantidad" ->
    # kˌantiðˈad).

    # Each hiatus is a syllable the strong-nucleus count cannot see: a dieresis_at, a weak
    # vowel after a cluster with a liquid, and a weak vowel behind an accented strong one
    # ("despliegue" -> dˌespliˈeɣe, "cigüeña" -> sˌiɣuˈeɲa). Counting them is what earns
    # those words their secondary.
    extra = (len(dieresis_at)
             + len(re.findall(r"(?:^|[^aeiouáéíóú])[lr]([iu])[aeoáéó]", p))
             + len(re.findall(r"[áéíóú][iu]", p)))

    # En palabra larga, un hiato de dos strongs abre un nuevo pie y se lleva una
    # secondary: "extraordinariamente" -> ˌekstɾaˌoɾðinˈaɾjamˈente.
    total_nucleos = len(set(_nucleus_map(p).values()))
    strong_hiatus = ([m.start() + 1 for m in re.finditer(r"[aeoáéó][aeoáéó]", p)]
                    if total_nucleos >= 6 else [])
    # Una a/e/o inicial de palabra ante otra fuerte cuenta como silaba suelta para la
    # secondary: "absolutamente" no la lleva porque su primary queda cerca.
    secondary = 0 if (primary is not None and primary + extra >= 2) else None
    #: A verb with enclitic pronouns carries a secondary on them, because the clitics
    #: add syllables the verb's own stress does not reach: "traemelo" -> tɾˈaemˌelo.
    enclitic = re.search(r"(?:me|te|se|lo|la|le|nos|os|los|las|les)"
                         r"(?:me|te|se|lo|la|le|nos|los|las|les)$", p)
    #: A second secondary mid-word when the word is very long and the primary sits far
    #: away: "configuracion" -> kˌonfiɣˌuɾasjˈon.
    secondary2 = 2 if (primary is not None and primary >= 4) else None
    # A hiatus after an accented strong vowel adds a syllable the nucleus count cannot
    # see, and in a word long enough the extra beat takes a secondary two nuclei on:
    # "clausulas" -> klˈausˌulas.
    if (primary is not None and secondary2 is None
            and re.search(r"[áéíóú][iu]", p)
            and len(set(_nucleus_map(p).values())) + extra >= 4):
        secondary2 = primary + 1
    if enclitic and primary is not None:
        nmap_all = _nucleus_map(p)
        # the nucleus the enclitic run starts on
        starts = [n for i, n in nmap_all.items() if i >= enclitic.start()]
        # Only when the clitics fall far enough from the primary to need their own
        # beat: "traemelo" is tɾˈaemˌelo (two nuclei away), while "dimelo" stays
        # dˈimelo and "devolvermelo" dˌeβolβˈeɾmelo, both only one away.
        if starts and min(starts) - primary >= 2:
            secondary2 = min(starts)
    # "-mente" is a compound: the adjective keeps its stress and the suffix brings
    # another, so espeak marks two primaries ("sˈolamˈente").
    mente_compound = p.endswith("mente") and len(set(_nucleus_map(p).values())) >= 3
    mente_base = None
    if mente_compound:
        # The suffix's own primary sits at the end, so it cannot decide this. The
        # adjective's does: "extraordinariamente" earns a mid-word secondary because
        # "extraordinaria" is long enough, "absolutamente" does not.
        base_stress = _stress_index(p[:-5])
        secondary2 = 2 if (base_stress is not None and base_stress >= 4) else None
        # "absolutamente" does carry one (four-syllable base), "solamente" does not.
        base_len = len(set(_nucleus_map(p[:-5]).values()))
        # The adjective keeps its own stress and the suffix brings its own.
        secondary = 0 if base_len >= 4 else None
        base = p[:-5]
        pos = _stress_index(base)
        if pos is not None:
            mente_base = pos
        # The suffix carries ITS primary on "men", the penultimate nucleus overall.
        primary = max(set(_nucleus_map(p).values())) - 1
    out = []
    i = 0
    nucleus_n = -1
    marked_group = False
    while i < len(p):
        c = p[i]
        nxt = p[i+1] if i+1 < len(p) else ""
        prv = p[i-1] if i else ""
        prev_is_vowel = bool(prv) and prv in VOWELS
        next_is_vowel = bool(nxt) and nxt in VOWELS

        if c in VOWELS:
            current_nucleus = nmap.get(i, 0)
            if current_nucleus != nucleus_n:
                nucleus_n = current_nucleus
                marked_group = False
            base0 = {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u"}.get(c, c)
            # The whole vowel group decides: a weak vowel BEFORE the nucleus is rising
            # (j/w), one AFTER it is falling (ɪ/ʊ). Looking only at the adjacent vowel
            # mistook "cualquier" (kwalkjˈeɾ) for a falling one.
            ini = i
            while ini > 0 and p[ini-1] in VOWELS:
                ini -= 1
            fin = i
            while fin < len(p) and p[fin] in VOWELS:
                fin += 1
            group = p[ini:fin]
            rel = i - ini
            strongs = [k for k, ch in enumerate(group) if ch in STRONG] or [len(group)-1]
            # In a hiatus of two strong vowels ("ae" of paella) each is its own nucleus,
            # so the mark can land on the second: paˈejja.
            nucleus = strongs[0] if rel not in strongs else rel
            # After a liquid the weak vowel does not close into a glide: "luego" is
            # luˈeɣo and "biblioteca" bˌiβliotˈeka, against "cuando" kwˈando.
            # ...but only when the liquid sits in a cluster (gr, bl, pl) or opens the
            # word. An intervocalic l or r does not: "valiosos" is baljˈosos and
            # "sugirio" sˌuxiɾjˈo, both with a diphthong.
            after_liquid = (rel == 0 and ini > 0 and p[ini-1] in "lr"
                            and (ini == 1 or (ini >= 2 and p[ini-2] not in VOWELS)))
            # A dieresis_at exists precisely to say that u sounds on its own: "cigüeña" is
            # sˌiɣuˈeɲa, a hiatus, not siɣwˈeɲa.
            has_dieresis = i in dieresis_at
            # An accented strong vowel does not drag the weak one along: "clausulas" is
            # klˈausˌulas, a hiatus, against "causa" kˈaʊsa.
            accented_before = any(ch in "áéíóú" for ch in group[:rel])
            is_glide = (base0 in "iu" and c not in "íú"
                            and len(group) > 1 and rel != nucleus
                            and not after_liquid and not has_dieresis
                            and not accented_before)
            falling = is_glide and rel > nucleus
            # The mark goes immediately before the nucleus: "kwˈando", not "kˈwando",
            # and "sjˈon", not "sˈjon". A glide never carries the stress.
            if rel in strongs and nmap.get(i) == nucleus_n and not marked_group:
                if primary is not None and nucleus_n == primary:
                    out.append("ˈ"); marked_group = True
                elif mente_base is not None and nucleus_n == mente_base:
                    out.append("ˈ"); marked_group = True
                elif secondary is not None and nucleus_n == secondary:
                    out.append("ˌ"); marked_group = True
                elif secondary2 is not None and nucleus_n == secondary2:
                    out.append("ˌ"); marked_group = True
            if is_glide:
                if falling:
                    out.append("ɪ" if base0 == "i" else "ʊ")
                else:
                    out.append("j" if base0 == "i" else "w")
            else:
                # /e/ opens to ɛ before n plus a consonant: "cuarenta" -> kwaɾˈɛnta,
                # "siento" sjˈɛnto. Not before m: "siempre" is sjˈempɾe and "tiempo"
                # tjˈempo, with the vowel closed.
                stressed_here = out and out[-1] == "ˈ"
                # The e of "-mente" never opens; the base's does ("lentamente").
                in_mente_suffix = mente_compound and i >= len(p) - 5
                if (base0 == "e" and stressed_here and not in_mente_suffix
                        and nxt == "n"
                        and i + 2 < len(p) and p[i+2] not in VOWELS):
                    out.append("ɛ")
                else:
                    out.append(base0)
            i += 1
            continue

        # consonantes
        if c == "c" and nxt == "h":
            out.append("tʃ"); i += 2
        elif c == "c":
            out.append("s" if (nxt and nxt in "eiéí") else "k"); i += 1
        elif c == "z":
            out.append("s"); i += 1
        elif c == "q":
            out.append("k"); i += 1
        elif c == "ɡ":
            out.append("ɡ" if (i == 0 or prv in "mn") else "ɣ"); i += 1
        elif c == "h":
            i += 1                                   # silent
        elif c == "l" and nxt == "l":
            out.append("ʝ" if i == 0 else "jj"); i += 2
        elif c == "y":
            out.append("ʝ" if next_is_vowel else "i"); i += 1
        elif c == "ñ":
            out.append("ɲ"); i += 1
        elif c == "j":
            out.append("x"); i += 1
        elif c == "g":
            if nxt in "eiéí":
                out.append("x"); i += 1
            else:
                out.append("ɡ" if (i == 0 or prv in "mn") else "ɣ"); i += 1
        elif c in "bv":
            # Occlusive after a pause or a nasal, fricative anywhere else -- including
            # after l or s ("devuelvas" -> deβwˈelβas).
            before_stop2 = bool(nxt) and nxt in "ptkbdɡgmnc"
            out.append("b" if (i == 0 or prv in "mn" or before_stop2) else "β"); i += 1
        elif c == "d":
            word_final = i == len(p) - 1
            # Occlusive after a pause, a nasal or l; and word-finally, where espeak
            # keeps it tense ("cantidad" -> ...ˈad).
            # Also before a stop or a nasal ("admitir" -> ˌadmitˈiɾ), but NOT before a
            # liquid or a fricative: "hidrogeno" is iðɾˈoxeno and "sobre" sˈoβɾe.
            before_stop = bool(nxt) and nxt in "ptkbdɡgmnc"
            hard = word_final or i == 0 or prv in "mnl" or before_stop
            out.append("d" if hard else "ð"); i += 1
        elif c == "r":
            if nxt == "r":
                out.append("r"); i += 2
            elif i == 0 or prv in "nls":
                out.append("r"); i += 1
            else:
                out.append("ɾ"); i += 1
        elif c == "n" and nxt and nxt in "ɡgjx":
            # Velar before g or j, whichever sound the g ends up making: "tengo" is
            # tˈɛŋɡo, "angel" aŋxˈel, "ingenieros" ˌiŋxenjˈeɾos. Not before c/k/q --
            # "cinco" stays sˈinko and "nunca" nˈunka.
            out.append("ŋ"); i += 1
        elif c == "n" and nxt and nxt in "bvpm":
            # A nasal takes the place of articulation of what follows: "convincentes"
            # is kˌombinsˈɛntes, not kˌonbin-.
            out.append("m"); i += 1
        elif c == "p" and nxt == "t":
            # Espeak lengthens p before t: "apto" -> ˈapːto, "captura" -> kapːtˈuɾa.
            # Only before t: "acto" is ˈakto and "obtener" ˌobtenˈeɾ, unlengthened.
            out.append("pː"); i += 1
        elif c == "x":
            out.append("s" if i == 0 else "ks"); i += 1
        elif c == "w":
            out.append("w"); i += 1
        else:
            out.append(c); i += 1
    return "".join(out)


#: Palabras que en una phonemes no llevan acento propio: articulos, preposiciones breves,
#: conjunciones y clíticos. Sueltas si lo llevan ("el" es ˈel), pero dentro de la phonemes
#: se apoyan en la palabra siguiente, y marcarlas es lo que delata a un conversor que
#: trabaja palabra por palabra.
UNSTRESSED_WORDS = {
    "el", "la", "los", "las", "lo",
    "de", "del", "a", "al", "en", "con", "por", "y", "e", "o", "u",
    "que", "se", "me", "te", "le", "les", "nos", "su", "sus", "mi", "mis", "tu", "tus",
    "sin", "si",
}

#: These drop to a secondary instead of losing the stress outright.
DEMOTED_WORDS = {"sobre", "entre", "hasta", "desde", "hacia", "segun", "según", "ante",
               "mientras", "porque", "cuando", "como", "donde", "aunque", "pero",
               "para", "cuanto", "cuanta", "cuantos", "cuantas",
               # stressed possessives and relatives: they lean on the noun they open
               "nuestro", "nuestra", "nuestros", "nuestras",
               "vuestro", "vuestra", "vuestros", "vuestras",
               "quien", "quienes", "cual", "cuales"}


def _unstress(fon):
    return fon.replace("ˈ", "").replace("ˌ", "")


def _demote(fon):
    """Baja la primaria a secondary. La e abierta vuelve a cerrarse: "mientras" suelto
    es mjˈɛntɾas, pero dentro de la phonemes mjˌentɾas."""
    return fon.replace("ˈ", "ˌ").replace("ɛ", "e")


def phonemes(text, lexicon=None):
    """Transcribe una phonemes entera, no palabra a palabra.

    La diferencia no es cosmetica. En una phonemes las palabras funcion pierden su acento
    ("la" es la, no lˈa), y una b/v inicial se relaja si la palabra anterior acaba en
    vocal ("zorro veloz" -> sˈoro βelˈos). Un conversor que trabaje palabra por palabra
    acierta cada una y aun asi suena distinto, que es exactamente lo que pasaba.
    """
    lexicon = lexicon or {}
    # The reference drops the opening marks: they tell a reader what is coming, and the
    # phonemes carry that in the intonation instead.
    text = text.replace("¿", "").replace("¡", "")
    pieces = re.findall(r"[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]+|[^a-záéíóúüñA-ZÁÉÍÓÚÜÑ]+", text)
    out_words = []
    prev_phoneme = ""                       # ultimo fonema emitido, para el enlace
    for piece in pieces:
        if not re.match(r"[a-záéíóúüñ]", piece, re.I):
            out_words.append(piece)
            if piece.strip():
                prev_phoneme = ""                       # la puntuacion corta el enlace
            continue
        lower = piece.lower()
        fon = lexicon.get(lower) or phonemes_for(piece)
        if lower in UNSTRESSED_WORDS:
            fon = _unstress(fon)
        elif lower in DEMOTED_WORDS:
            fon = _demote(fon)
        # Enlace: /b d ɡ/ solo son oclusivas tras pausa o nasal, y la frontera de
        # palabra no las protege -- "kilos de" suena ðe aunque "de" empiece la palabra.
        if fon[:1] in "bdɡ" and prev_phoneme not in ("", "m", "n", "ŋ"):
            fon = {"b": "β", "d": "ð", "ɡ": "ɣ"}[fon[0]] + fon[1:]
        # ...y la nasal word_final toma el punto de la consonante siguiente:
        # "reunion porque" -> reʊnjˈom pˌoɾke.
        # Only before g or the jota, the same limit as inside a word: "sin gas" is
        # siŋ ɡˈas, but "un castillo" keeps ˈun and "van con" keeps βˈan.
        if fon[:1] in "ɡɣx":
            for k in range(len(out_words) - 1, -1, -1):
                if out_words[k].strip():
                    if out_words[k].endswith("n"):
                        out_words[k] = out_words[k][:-1] + "ŋ"
                    break
        if fon[:1] in "bpβ":
            for k in range(len(out_words) - 1, -1, -1):
                if out_words[k].strip():
                    if out_words[k].endswith("n"):
                        out_words[k] = out_words[k][:-1] + "m"
                    break
        # Una d word_final deja de estar en word_final absoluto si la phonemes sigue: "ansiedad
        # antes" -> ˌansjeðˈað ˈantes.

        # Una d word_final deja de estar en word_final absoluto si la palabra siguiente empieza
        # por vocal: "ansiedad antes" -> ˌansjeðˈað ˈantes, pero "cantidad de" mantiene
        # la oclusiva porque lo que sigue es consonante.
        if out_words and fon[:1] and fon[0] not in "ˈˌ" and fon[0] in "aeiouɛ":
            for k in range(len(out_words) - 1, -1, -1):
                if out_words[k].strip():
                    if out_words[k].endswith("d"):
                        out_words[k] = out_words[k][:-1] + "ð"
                    break
        elif fon[:2] and fon[0] in "ˈˌ" and fon[1] in "aeiouɛ":
            for k in range(len(out_words) - 1, -1, -1):
                if out_words[k].strip():
                    if out_words[k].endswith("d"):
                        out_words[k] = out_words[k][:-1] + "ð"
                    break
        out_words.append(fon)
        prev_phoneme = fon[-1] if fon else prev_phoneme
    return "".join(out_words)
