"""Capa conversacional — estados, ritmo, variabilidad y planificación.

Verifica lo que hace que la llamada NO suene a chatbot por turnos: estados con
comportamiento propio, pausas siempre distintas, formulaciones que no se
repiten, respuestas en varias etapas, narración solo cuando hay trabajo real y
contenido de negocio intacto.
"""

import asyncio
import random
from array import array

import pytest

from services.voice.conversation import (
    PHRASE_BANK,
    AmbientSoundManager,
    BackchannelManager,
    BehaviorEngine,
    ConversationEngine,
    ConversationMemory,
    ConversationState,
    ConversationStateManager,
    ConversationTimingEngine,
    PauseLength,
    PauseManager,
    PhraseManager,
    SegmentKind,
    SpeechIntent,
    SpeechPlanner,
    SpeechRenderer,
    SpeechRequest,
    profile_for,
    silence,
    split_ideas,
)


def _engine(seed: int = 7) -> ConversationEngine:
    return ConversationEngine("test-call", rng=random.Random(seed))


def _spoken(plan) -> list[str]:
    return [s.text for s in plan.speech_segments]


# ── Conversation State Manager ────────────────────────────────────────────────


def test_all_required_states_exist():
    required = {
        "LISTENING", "UNDERSTANDING", "PROCESSING", "SEARCHING",
        "CONFIRMING", "WAITING_USER", "CLOSING",
    }
    assert required <= {s.name for s in ConversationState}


def test_each_state_has_its_own_behaviour_not_just_phrases():
    profiles = [profile_for(s) for s in ConversationState]
    # Ritmo, tiempo de reacción y expresividad difieren entre estados.
    assert len({p.pause_scale for p in profiles}) >= 4
    assert len({p.reaction_range for p in profiles}) >= 4
    assert len({p.max_stages for p in profiles}) >= 2
    # Solo los estados de trabajo admiten fondo contextual.
    ambient = {p.state for p in profiles if p.ambient_allowed}
    assert ambient == {ConversationState.PROCESSING, ConversationState.SEARCHING}


def test_state_manager_tracks_transitions():
    sm = ConversationStateManager("uuid")
    assert sm.state is ConversationState.LISTENING
    sm.user_turn_ended()
    assert sm.state is ConversationState.UNDERSTANDING
    sm.working(searching=True)
    assert sm.state is ConversationState.SEARCHING
    sm.for_intent(SpeechIntent.CONFIRM_PICKUP)
    assert sm.state is ConversationState.CONFIRMING
    sm.for_intent(SpeechIntent.SERVICE_CREATED)
    assert sm.state is ConversationState.CLOSING
    assert ConversationState.SEARCHING in sm.history


# ── Conversation Memory + Phrase Manager ─────────────────────────────────────


def test_every_intent_category_has_several_formulations():
    for category, variants in PHRASE_BANK.items():
        assert len(variants) >= 3, f"{category} tiene muy poca variedad"
        assert len({v.key for v in variants}) == len(variants)


def test_phrase_manager_never_reuses_a_recent_formulation():
    memory = ConversationMemory()
    phrases = PhraseManager(memory, random.Random(1))
    seen = []
    for _ in range(40):
        picked = phrases.pick("ack")
        assert picked
        # Ninguna de las últimas formulaciones vuelve a salir de inmediato.
        assert picked[0] not in seen[-3:]
        seen.append(picked[0])
    assert len(set(seen)) >= 5


def test_two_consecutive_calls_do_not_repeat_the_same_phrases():
    greetings = set()
    for seed in range(20):
        engine = _engine(seed)
        plan = engine.plan(
            SpeechRequest(intent=SpeechIntent.GREETING, after_user_turn=False)
        )
        greetings.add(plan.text)
    assert len(greetings) >= 3


def test_memory_blocks_repeating_the_same_syntactic_construction():
    memory = ConversationMemory()
    phrases = PhraseManager(memory, random.Random(3))
    forms = []
    for _ in range(12):
        phrases.pick("confirm_pickup_barrio", place="Calle 5", barrio="Pubenza")
        forms.append(memory.recent_forms("confirm_pickup_barrio")[-1])
    for a, b in zip(forms, forms[1:]):
        assert a != b


# ── Pause Manager ─────────────────────────────────────────────────────────────


def test_pauses_are_never_constant_and_stay_in_range():
    memory = ConversationMemory()
    pauses = PauseManager(memory, random.Random(5))
    values = [
        pauses.duration(length)
        for _ in range(60)
        for length in (PauseLength.MICRO, PauseLength.SHORT, PauseLength.MEDIUM)
    ]
    assert all(0.05 <= v <= 1.2 for v in values)
    for a, b in zip(values, values[1:]):
        assert a != b
    assert len(set(values)) > 20


def test_pause_lengths_are_ordered_on_average():
    memory = ConversationMemory()
    pauses = PauseManager(memory, random.Random(11))

    def mean(length):
        return sum(pauses.duration(length) for _ in range(80)) / 80

    micro, short, medium, long_ = (
        mean(PauseLength.MICRO), mean(PauseLength.SHORT),
        mean(PauseLength.MEDIUM), mean(PauseLength.LONG),
    )
    assert micro < short < medium < long_


def test_pause_between_ideas_depends_on_the_next_idea():
    memory = ConversationMemory()
    pauses = PauseManager(memory, random.Random(2))
    short_idea = sum(pauses.between_ideas("Listo.") for _ in range(40)) / 40
    long_idea = sum(
        pauses.between_ideas(
            "El conductor te llama para afinar el punto exacto de recogida"
        )
        for _ in range(40)
    ) / 40
    assert short_idea < long_idea


# ── Conversation Timing Engine ────────────────────────────────────────────────


def test_reaction_time_is_human_variable_and_bounded():
    timing = ConversationTimingEngine(random.Random(9))
    profile = profile_for(ConversationState.CONFIRMING)
    values = [timing.reaction_delay(profile) for _ in range(40)]
    lo, hi = profile.reaction_range
    assert all(lo <= v <= hi for v in values)
    assert len(set(values)) > 10
    for a, b in zip(values, values[1:]):
        assert a != b


def test_no_second_delay_once_the_turn_already_spoke():
    timing = ConversationTimingEngine(random.Random(4))
    profile = profile_for(ConversationState.CONFIRMING)
    assert timing.reaction_delay(profile, already_speaking=True) == 0.0
    assert timing.reaction_delay(profile, user_turn=False) == 0.0


def test_wait_interval_grows_and_is_never_a_metronome():
    timing = ConversationTimingEngine(random.Random(6))
    first = [timing.wait_check_interval(0) for _ in range(20)]
    second = [timing.wait_check_interval(1) for _ in range(20)]
    assert len(set(first)) > 5
    assert sum(second) / len(second) > sum(first) / len(first)


# ── Behavior Engine ───────────────────────────────────────────────────────────


def test_narration_requires_real_work():
    memory = ConversationMemory()
    behavior = BehaviorEngine(memory, random.Random(1))
    profile = profile_for(ConversationState.SEARCHING)
    request = SpeechRequest(intent=SpeechIntent.CONFIRM_PICKUP, did_work=False)
    for _ in range(50):
        decision = behavior.decide(request, profile, reaction=0.1)
        assert not decision.use_narration
        assert not decision.use_transition
        assert not decision.use_ambient
        assert not decision.use_found


def test_never_two_fillers_back_to_back_inside_one_response():
    memory = ConversationMemory()
    behavior = BehaviorEngine(memory, random.Random(2))
    behavior.backchannel.capture_closed()   # Lyra tiene la palabra
    profile = profile_for(ConversationState.SEARCHING)
    request = SpeechRequest(intent=SpeechIntent.CONFIRM_PICKUP, did_work=True)
    for _ in range(200):
        decision = behavior.decide(request, profile, reaction=0.1)
        if decision.use_ack and decision.use_transition:
            assert decision.use_narration, "dos rellenos quedarían pegados"


def test_filler_frequency_collapses_after_consecutive_use():
    memory = ConversationMemory()
    behavior = BehaviorEngine(memory, random.Random(3))
    behavior.backchannel.capture_closed()
    profile = profile_for(ConversationState.SEARCHING)
    request = SpeechRequest(intent=SpeechIntent.CONFIRM_PICKUP, did_work=True)
    memory.note_turn(used_filler=True)
    memory.note_turn(used_filler=True)
    for _ in range(50):
        decision = behavior.decide(request, profile, reaction=0.1)
        assert not decision.uses_filler


def test_pace_of_a_state_varies_between_turns():
    memory = ConversationMemory()
    behavior = BehaviorEngine(memory, random.Random(8))
    profile = profile_for(ConversationState.CONFIRMING)
    request = SpeechRequest(intent=SpeechIntent.CONFIRM_PICKUP)
    scales = {
        behavior.decide(request, profile, reaction=0.1).pause_scale
        for _ in range(30)
    }
    assert len(scales) > 10


# ── Speech Planner ────────────────────────────────────────────────────────────


def test_important_answers_come_out_in_several_stages_with_pauses():
    engine = _engine(3)
    engine.begin_turn()
    plan = engine.plan(
        SpeechRequest(
            intent=SpeechIntent.CONFIRM_PICKUP,
            text="¿Calle 5 #3-45, barrio Pubenza, es correcto?",
            slots={"place": "Calle 5 #3-45", "barrio": "Pubenza"},
            did_work=True,
            kind="address",
        )
    )
    assert len(plan.speech_segments) >= 2, "salió como un bloque monolítico"
    pauses = [s for s in plan.segments if s.kind is SegmentKind.PAUSE]
    assert pauses, "no hay pausas entre ideas"
    # Las pausas van ENTRE etapas, no solo al final.
    kinds = [s.kind for s in plan.segments]
    assert SegmentKind.PAUSE in kinds[: len(kinds) - 1]


def test_business_content_is_never_rewritten():
    engine = _engine(4)
    engine.begin_turn()
    plan = engine.plan(
        SpeechRequest(
            intent=SpeechIntent.CONFIRM_PICKUP,
            slots={"place": "Cra. 4 #70AN-09", "barrio": "La Paz"},
            did_work=True,
        )
    )
    assert "Cra. 4 #70AN-09" in plan.text
    assert "La Paz" in plan.text


def test_payload_only_intents_keep_the_literal_text():
    engine = _engine(5)
    engine.begin_turn()
    message = "¿Te refieres a Santa Teresa o a Prados del Norte?"
    plan = engine.plan(
        SpeechRequest(intent=SpeechIntent.DISAMBIGUATE, text=message)
    )
    assert message in plan.text


def test_an_address_is_never_split_in_the_middle():
    ideas = split_ideas(
        "El punto de recogida es Calle 8C #17-55, barrio La Esmeralda, Popayán."
    )
    assert any("Calle 8C #17-55" in i for i in ideas)
    for idea in ideas:
        assert not idea.endswith("#")


def test_long_payload_is_broken_into_ideas():
    ideas = split_ideas(
        "Listo, te ubico en el barrio Pubenza. El conductor te llamará para "
        "afinar el punto exacto. Un momento por favor."
    )
    assert len(ideas) >= 3


def test_ambient_sound_only_appears_when_there_is_a_real_action():
    engine = _engine(1)
    for _ in range(30):
        engine.begin_turn()
        plan = engine.plan(
            SpeechRequest(
                intent=SpeechIntent.CONFIRM_PICKUP,
                slots={"place": "Pubenza"},
                did_work=False,
            )
        )
        assert not [s for s in plan.segments if s.kind is SegmentKind.AMBIENT]

    saw_ambient = False
    for seed in range(40):
        e = _engine(seed)
        e.begin_turn()
        plan = e.plan(e.narration("address"))
        if any(s.kind is SegmentKind.AMBIENT for s in plan.segments):
            saw_ambient = True
            break
    assert saw_ambient, "el fondo contextual nunca aparece con trabajo real"


def test_silence_budget_keeps_the_response_from_dragging():
    for seed in range(30):
        engine = _engine(seed)
        engine.begin_turn()
        plan = engine.plan(
            SpeechRequest(
                intent=SpeechIntent.HANDOFF,
                text="Listo, te ubico en el barrio Pubenza.",
                slots={"barrio": "Pubenza"},
                did_work=True,
                kind="geo_context",
            )
        )
        assert plan.silence_seconds <= 1.85


def test_response_length_varies_across_turns():
    engine = _engine(12)
    lengths = set()
    for _ in range(20):
        engine.begin_turn()
        plan = engine.plan(
            SpeechRequest(
                intent=SpeechIntent.CONFIRM_PICKUP,
                slots={"place": "Pubenza"},
                did_work=True,
                kind="place",
            )
        )
        lengths.add(len(plan.speech_segments))
    assert len(lengths) >= 2, "todas las respuestas tienen el mismo tamaño"


def test_planner_falls_back_to_literal_text_without_slots():
    memory = ConversationMemory()
    rng = random.Random(0)
    planner = SpeechPlanner(
        PhraseManager(memory, rng),
        PauseManager(memory, rng),
        ConversationTimingEngine(rng),
        BehaviorEngine(memory, rng),
        memory,
    )
    plan = planner.plan(
        SpeechRequest(intent=SpeechIntent.CONFIRM_PICKUP, text="¿Pubenza es correcto?"),
        profile_for(ConversationState.CONFIRMING),
    )
    assert "¿Pubenza es correcto?" in plan.text


def test_empty_request_produces_no_speech():
    engine = _engine(2)
    plan = engine.plan(SpeechRequest(intent=SpeechIntent.REPROMPT, text=""))
    assert plan.segments == ()
    assert plan.text == ""


# ── Ambient Sound Manager ─────────────────────────────────────────────────────


def test_ambient_bed_is_audible_but_stays_under_the_voice():
    """Tiene que oírse por teléfono y a la vez no competir con la voz."""
    ambient = AmbientSoundManager(random.Random(1), sample_rate=8000)
    for kind in ambient.kinds():
        pcm = ambient.bed(kind, 0.5)
        assert len(pcm) == int(0.5 * 8000) * 2
        samples = array("h")
        samples.frombytes(pcm)
        peak = max(abs(s) for s in samples)
        # Audible: muy por encima del piso de cuantización de la banda estrecha.
        assert peak >= int(0.03 * 32767), f"{kind} no se oiría por teléfono"
        # Subordinado: bien por debajo del nivel de la voz sintetizada.
        assert peak <= int(0.16 * 32767), f"{kind} compite con la voz"


def test_ambient_has_transients_over_a_quieter_bed():
    """No es un zumbido plano: hay golpes (teclas/clics) sobre un lecho tenue."""
    ambient = AmbientSoundManager(random.Random(4), sample_rate=8000)
    samples = array("h")
    samples.frombytes(ambient.bed("typing", 1.5))
    peak = max(abs(s) for s in samples)
    mean = sum(abs(s) for s in samples) / len(samples)
    assert peak > mean * 6, "sin transitorios audibles no suena a trabajo"


def test_every_narration_kind_maps_to_a_real_texture():
    from services.voice.conversation import ambient_for

    ambient = AmbientSoundManager(random.Random(1))
    for kind in ("address", "place", "geo_context", "service", "generic", ""):
        assert ambient_for(kind) in ambient.kinds()


def test_ambient_bed_is_empty_without_duration():
    ambient = AmbientSoundManager(random.Random(1))
    assert ambient.bed("typing", 0.0) == b""


# ── Speech Renderer ───────────────────────────────────────────────────────────


def _fake_synth(byte_map=None):
    async def synth(text: str) -> bytes:
        length = (byte_map or {}).get(text, 160)
        return b"\x01\x02" * length
    return synth


def test_renderer_builds_one_buffer_with_pauses_inside():
    engine = _engine(6)
    engine.begin_turn()
    plan = engine.plan(
        SpeechRequest(
            intent=SpeechIntent.CONFIRM_PICKUP,
            slots={"place": "Pubenza"},
            did_work=True,
            kind="place",
        )
    )
    rendered = asyncio.run(engine.render(plan, _fake_synth()))

    speech_bytes = len(plan.speech_segments) * 160 * 2
    silence_bytes = sum(
        len(silence(s.duration))
        for s in plan.segments
        if s.kind is SegmentKind.PAUSE
    )
    ambient_bytes = sum(
        int(s.duration * 8000) * 2
        for s in plan.segments
        if s.kind is SegmentKind.AMBIENT
    )
    assert len(rendered.pcm) == speech_bytes + silence_bytes + ambient_bytes
    assert rendered.text == plan.text
    assert len(rendered.marks) == len(plan.speech_segments)


def test_render_marks_are_monotonic_for_barge_in():
    engine = _engine(10)
    engine.begin_turn()
    plan = engine.plan(
        SpeechRequest(
            intent=SpeechIntent.HANDOFF,
            slots={"barrio": "Pubenza"},
            did_work=True,
        )
    )
    rendered = asyncio.run(engine.render(plan, _fake_synth()))
    offsets = [m[0] for m in rendered.marks]
    assert offsets == sorted(offsets)
    assert all(text in plan.text for _, text in rendered.marks)


def test_renderer_propagates_synthesis_failure():
    engine = _engine(13)
    engine.begin_turn()
    plan = engine.plan(
        SpeechRequest(intent=SpeechIntent.REPROMPT, text="¿Me repites, por favor?")
    )

    async def broken(_text: str) -> bytes:
        raise RuntimeError("tts caído")

    with pytest.raises(RuntimeError):
        asyncio.run(engine.render(plan, broken))


def test_empty_plan_renders_nothing():
    renderer = SpeechRenderer()
    engine = _engine(14)
    plan = engine.plan(SpeechRequest(intent=SpeechIntent.REPROMPT, text=""))
    rendered = asyncio.run(renderer.render(plan, _fake_synth()))
    assert not rendered and rendered.pcm == b""


# ── Motor completo ────────────────────────────────────────────────────────────


def test_greeting_starts_without_delay_and_in_stages():
    engine = _engine(21)
    plan = engine.plan(
        SpeechRequest(intent=SpeechIntent.GREETING, after_user_turn=False)
    )
    assert len(plan.speech_segments) >= 2
    assert plan.segments[0].kind is SegmentKind.SPEECH  # sin retardo inicial
    assert "Lyra" in plan.text


def test_turn_reaction_applies_once_per_turn():
    engine = _engine(22)
    engine.begin_turn()
    first = engine.plan(engine.narration("address"))
    second = engine.plan(
        SpeechRequest(
            intent=SpeechIntent.CONFIRM_PICKUP,
            slots={"place": "Pubenza"},
            did_work=True,
        )
    )
    assert second.segments[0].kind is SegmentKind.SPEECH
    assert first.state == ConversationState.SEARCHING.value
    assert second.state == ConversationState.CONFIRMING.value


def test_wait_more_never_repeats_the_same_phrase():
    engine = _engine(23)
    engine.begin_turn()
    said = []
    for _ in range(6):
        plan = engine.plan(engine.wait_more("address"))
        said.append(plan.text)
    for a, b in zip(said, said[1:]):
        assert a != b


def test_engine_keeps_a_conversational_state_per_call():
    a, b = _engine(1), _engine(1)
    a.begin_turn()
    a.plan(SpeechRequest(intent=SpeechIntent.GREETING, after_user_turn=False))
    assert a.memory.turns >= 1
    assert b.memory.turns == 0


# ── Backchannel Manager ───────────────────────────────────────────────────────


def test_backchannel_only_fires_when_it_cannot_hurt_capture():
    memory = ConversationMemory()
    bc = BackchannelManager(memory, random.Random(1))

    # Canal de captura abierto (el usuario tiene la palabra): jamás.
    assert bc.capture_open and not bc.is_safe
    assert not any(
        bc.should_emit(after_user_turn=True, probability=1.0) for _ in range(50)
    )

    # Lyra tiene la palabra pero está reproduciendo audio: tampoco.
    bc.capture_closed()
    bc.playback_started()
    assert not bc.is_safe
    assert not bc.should_emit(after_user_turn=True, probability=1.0)

    # Ventana segura: recién ahí puede salir la señal de escucha.
    bc.playback_finished()
    assert bc.is_safe
    assert bc.should_emit(after_user_turn=True, probability=1.0)
    # Y nunca si el turno no responde al usuario (avisos, cierres).
    assert not bc.should_emit(after_user_turn=False, probability=1.0)


def test_engine_closes_the_backchannel_window_while_user_speaks():
    engine = _engine(31)
    engine.begin_turn()
    assert engine.backchannel.is_safe
    engine.user_speaking()
    assert not engine.backchannel.is_safe

    # Con el usuario hablando, ninguna decisión habilita la señal de escucha.
    request = SpeechRequest(
        intent=SpeechIntent.CONFIRM_PICKUP, slots={"place": "Pubenza"}, did_work=True
    )
    profile = profile_for(ConversationState.CONFIRMING)
    for _ in range(50):
        assert not engine.behavior.decide(request, profile, reaction=0.0).use_ack

    # Al cerrarse la captura vuelve a ser posible.
    engine.begin_turn()
    assert any(
        engine.behavior.decide(request, profile, reaction=0.0).use_ack
        for _ in range(50)
    )


# ── Aviso inmediato de trabajo ────────────────────────────────────────────────


def test_work_announcement_starts_instantly_and_carries_the_bed():
    """El aviso de "voy a buscar" no puede empezar con silencio ni con adornos:
    tiene que sonar antes de que arranque la búsqueda, y detrás va el fondo de
    trabajo que cubre la consulta real."""
    for seed in range(15):
        engine = _engine(seed)
        engine.begin_turn()
        plan = engine.plan(engine.narration("address"))

        assert plan.segments[0].kind is SegmentKind.SPEECH, "el aviso arranca con pausa"
        assert len(plan.speech_segments) == 1, "el aviso trae relleno de más"

        beds = [s for s in plan.segments if s.kind is SegmentKind.AMBIENT]
        assert len(beds) == 1, "el aviso no lleva fondo de trabajo"
        assert 1.5 <= beds[0].duration <= 3.5
        assert beds[0].ambient == "typing"


def test_wait_phrases_also_carry_a_bed_and_no_delay():
    engine = _engine(2)
    engine.begin_turn()
    plan = engine.plan(engine.wait_more("geo_context"))
    assert plan.segments[0].kind is SegmentKind.SPEECH
    beds = [s for s in plan.segments if s.kind is SegmentKind.AMBIENT]
    assert len(beds) == 1 and beds[0].ambient == "clicks"


def test_a_response_never_carries_more_than_one_decoration():
    """Dos adornos ya suenan a relleno y retrasan lo que el usuario espera."""
    from services.voice.conversation import PHRASE_BANK

    decorations = {
        part
        for category in ("ack", "transition", "found")
        for phrase in PHRASE_BANK[category]
        for part in phrase.parts
    }
    for seed in range(40):
        engine = _engine(seed)
        engine.begin_turn()
        plan = engine.plan(
            SpeechRequest(
                intent=SpeechIntent.CONFIRM_PICKUP,
                slots={"place": "Pubenza", "barrio": "Pubenza"},
                did_work=True,
                kind="place",
            )
        )
        spoken = _spoken(plan)
        # Solo cuenta como decoración lo que va ANTES del contenido de negocio.
        leading = 0
        for stage in spoken:
            if stage in decorations and "Pubenza" not in stage:
                leading += 1
            else:
                break
        assert leading <= 1
