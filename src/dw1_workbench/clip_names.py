"""Evidence-backed names for MMD animation clip IDs.

The game stores no clip names. These come from where the game code starts each animation ID
(jype0/dw_decomp aaf69a4, cross-checked with DW1-SydPatches b9671fa). "confirmed" means the call
sites leave one clear meaning; "likely" means the ID is shared by several situations and the name
describes them. The player character (resource ID 0) has its own animation set.
"""
from __future__ import annotations

from dataclasses import dataclass

# SydPatches Entity.cpp entityGetTechFromAnim: animation 46 + i plays technique moves[i].
TECH_ANIMATION_START = 46
PLAYER_RESOURCE_ID = 0


@dataclass(frozen=True)
class ClipName:
    name: str
    confidence: str  # "confirmed" or "likely"
    evidence: str


def _table(rows):
    return {clip: ClipName(name, confidence, evidence) for clip, name, confidence, evidence in rows}


DIGIMON_CLIPS = _table([
    (0, "Idle", "confirmed", "partnerTickIdle, setPartnerIdle (healthy), NPCEntityTickLookingAtTamer; default reset"),
    (1, "Idle (unwell)", "confirmed", "setPartnerIdle when sleepy, tired, injured or sick"),
    (2, "Walk", "confirmed", "setPartnerSlowWalking (healthy), NPCEntityTickTrackingTamer, walking to food/toilet"),
    (3, "Walk (unwell)", "confirmed", "setPartnerSlowWalking when sleepy, tired, injured or sick"),
    (4, "Run", "confirmed", "partnerTickWalking catching up, NPCEntityTickTrackingTamer3, tickPartnerToilet"),
    (5, "Happy", "confirmed", "updateConditionAnimation: happiness >= 31"),
    (7, "Unhappy mood", "confirmed", "updateConditionAnimation: happiness < -30"),
    (8, "Eat", "confirmed", "partnerTickFeedItem (item removed at frame 11, then consumed), partnerTickEatShit"),
    (9, "Sleep", "confirmed", "partnerTickSleep"),
    (10, "Poop", "confirmed", "tickPartnerToilet, tickWildPoop"),
    (11, "Reaction (after eating, before sleeping, after evolving)", "likely",
     "partnerHandleFoodFeed, partnerTickSleep, EVL_tickEvoSequence end, DOOA_tickRebirth"),
    (12, "Reaction (evolution climax, MP training, after wild poop, dissolving)", "likely",
     "EVL_tickEvoSequence, TRN2_tickMpTraining, tickWildPoop, DOOA_tickDissolve"),
    (13, "Refuse item", "confirmed", "partnerTickFeedItem: item put away without being consumed"),
    (14, "Injured or sick", "confirmed", "updateConditionAnimation: condition injured/sick"),
    (15, "Hungry", "confirmed", "updateConditionAnimation: condition hungry"),
    (16, "Needs toilet", "confirmed", "updateConditionAnimation: condition poopy"),
    (17, "Sleepy", "confirmed", "updateConditionAnimation: condition sleepy"),
    (18, "Tired", "confirmed", "updateConditionAnimation: condition tired; end of training sessions"),
    (19, "Unhappy condition", "confirmed", "updateConditionAnimation: condition unhappy; BTL_tickSenileState"),
    (22, "Brains training", "likely", "TRN_tickBrainsTraining"),
    (27, "HP training", "likely", "TRN_tickHpTraining, TRN2_tickHpTraining"),
    (28, "Curling aim", "likely", "KAR_beginAiming; also DOOA_tickRebirth"),
    (29, "Curling throw", "likely", "KAR_beginThrow"),
    (30, "Curling result", "likely", "KAR_tickMatchState, KAR_moveMatchCamera"),
    (31, "Curling throw (second)", "likely", "KAR_beginThrow"),
    (32, "Curling result (second)", "likely", "KAR_tickMatchState, KAR_moveMatchCamera"),
    (33, "Battle stance", "confirmed", "loadBattleData, BTL_handlePartnerMoveCommand, battle start"),
    (34, "Stunned", "confirmed", "BTL_tickStunState, STD_tickStunState, VS__tickDigimonStun"),
    (35, "Recover from flattened", "likely",
     "BTL_tickEnemyAI when the flattened status ends; also curling aiming and camera chases; "
     "the game zeroes sideways root motion for this ID"),
    (36, "Retreat / flee", "likely",
     "BTL_handleBattleIntro (bystanders flee), after revive in battle; the game zeroes sideways root motion"),
    (37, "Block", "confirmed", "BTL_tickAttackHits sets FIGHTER_FLAG_BLOCKING; defense training"),
    (40, "Hit from the front", "confirmed", "BTL_handleHitReaction: attacker within 90 degrees of facing"),
    (41, "Hit from behind", "confirmed", "BTL_handleHitReaction: attacker behind"),
    (42, "Victory", "confirmed", "BTL_handleVictorySequence, STD_tickChampionScene"),
    (43, "Faint", "confirmed", "BTL_faintDigimon, STD_faintDigimon"),
    (44, "Revive", "confirmed", "handleRestore at 0 HP, DOOA_tickDissolve, battle revive"),
])

PLAYER_CLIPS = _table([
    (0, "Idle", "confirmed", "tamerTickIdle, tamerTickWalkingState"),
    (1, "Battle stance", "confirmed", "loadBattleData, tamerTickBattle"),
    (2, "Walk", "confirmed", "tamerTickWalkingState"),
    (3, "Run", "confirmed", "tamerTickWalkingState"),
    (5, "Hold out item", "likely", "partnerTickFeedItem (tamer offers the item)"),
    (6, "Throw item", "confirmed", "startThrowingItem"),
    (7, "Praise or scold", "likely", "tamerTickPraiseScold"),
    (8, "Put partner to sleep", "likely", "partnerTickSleep (tamer)"),
    (10, "Training", "likely", "tamerTickTraining, tamerTickBattle"),
    (12, "Pick up item", "confirmed", "tamerTickPickupItem"),
    (14, "Battle command", "likely", "handleCommands"),
    (38, "Praise", "likely", "tamerTickPraiseScold"),
    (39, "Scold", "likely", "tamerTickPraiseScold"),
])


def clip_name(clip_index: int, resource_id: int, moves: tuple[int, ...] = (),
              move_names: list[str | None] | None = None) -> ClipName | None:
    """Name for one clip of one character, or None when no evidence names it."""
    if resource_id == PLAYER_RESOURCE_ID:
        return PLAYER_CLIPS.get(clip_index)
    if clip_index >= TECH_ANIMATION_START:
        slot = clip_index - TECH_ANIMATION_START
        if slot >= len(moves) or moves[slot] == 0xFF:
            return None
        move = moves[slot]
        name = (move_names[move] if move_names and move < len(move_names) else None) or f"technique {move}"
        return ClipName(f"Technique: {name}", "confirmed",
                        f"entityGetTechFromAnim: animation {TECH_ANIMATION_START} + {slot} plays moves[{slot}] = "
                        f"{move}; name from MOVE_NAMES")
    return DIGIMON_CLIPS.get(clip_index)
