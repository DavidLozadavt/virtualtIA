from typing import Union, Tuple, List
from twilio.twiml.voice_response import VoiceResponse, Gather

def build_gather_response(
    message: str, 
    action_url: str, 
    hints: Union[Tuple[str, ...], List[str], str],
    voice: str = "Polly.Mia",
    speech_timeout: str = "auto",
    gather_timeout: int = 25
) -> str:
    """
    Builds a TwiML Gather XML response.
    
    This function is pure. It depends only on the passed arguments.
    """
    response = VoiceResponse()
    
    # Format hints
    if isinstance(hints, (tuple, list)):
        hints_str = ", ".join(hints)
    else:
        hints_str = str(hints)
        
    gather = Gather(
        input="speech",
        language="es-CO",
        speech_timeout=speech_timeout,
        timeout=gather_timeout,
        action=action_url,
        method="POST",
        profanity_filter="false",
        speech_model="phone_call",
        enhanced="true",
        hints=hints_str
    )
    gather.say(message, voice=voice, language="es-MX")
    response.append(gather)
    
    # CRITICAL: If Gather times out (no speech detected), redirect back
    # to process_speech with empty SpeechResult so silence handler runs
    response.redirect(action_url, method="POST")
    
    # The twilio-python library handles XML encoding declaration properly
    return str(response)

def build_say_hangup(message: str, voice: str = "Polly.Mia") -> str:
    """
    Builds a TwiML Say + Hangup response.
    
    This function is pure. It depends only on the passed arguments.
    """
    response = VoiceResponse()
    response.say(message, voice=voice, language="es-MX")
    response.hangup()
    
    return str(response)
