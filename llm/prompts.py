"""
Prompt Templates for FPL Graph-RAG System
"""
from typing import Dict, Any, Optional, List

from config.settings import SEASONS, CURRENT_SEASON

# Season wording for prompts, derived so it stays right as seasons are added
_FIRST, _LAST, _N = SEASONS[0], SEASONS[-1], len(SEASONS)
_ALL_SEASONS = ", ".join(SEASONS)
_PREV_SEASON = SEASONS[-2] if CURRENT_SEASON == _LAST and _N > 1 else _LAST


class PromptTemplates:
    """Collection of prompt templates for different use cases."""
    
    # System personas
    PERSONAS = {
        "fpl_expert": """You are an expert Fantasy Premier League (FPL) analyst with deep knowledge of player statistics, 
team formations, and game strategies. You provide accurate, data-driven insights based on the information provided.""",
        
        "trivia_master": """You are an entertaining FPL Trivia Master who makes learning about Fantasy Premier League fun. 
You provide accurate answers with interesting facts and context.""",
        
        "transfer_advisor": """You are a strategic FPL Transfer Advisor who helps managers make optimal transfer decisions. 
You consider form, fixtures, value, and team balance when making recommendations.""",
        
        "stats_analyst": """You are a detailed FPL Statistics Analyst who excels at breaking down player and team performance metrics. 
You present data clearly and draw meaningful conclusions."""
    }

    # Friendly, conversational persona to improve user interactions
    PERSONAS["conversational_fpl"] = (
        "You are a helpful and responsive Fantasy Premier League (FPL) assistant. "
        "You listen carefully to what users want and respond appropriately to their level of interest. "
        "When users give vague or uncertain responses ('hmm', 'i dont know', 'not sure'), you offer helpful options rather than forcing information on them. "
        "You're knowledgeable but respectful - you don't overwhelm users with unwanted facts. "
        "Keep responses natural and conversational (2-3 sentences max)."
    )
    
    # Enhanced persona for Llama 3.3 70B with better reasoning
    PERSONAS["conversational_fpl_llama"] = (
        "You are an intelligent and context-aware Fantasy Premier League (FPL) assistant powered by a large language model. "
        "You excel at understanding user intent and responding appropriately: "
        "- When users are genuinely curious, you provide insightful analysis with relevant comparisons "
        "- When users are uncertain or uninterested ('hmm', 'i dont know'), you acknowledge this and offer choices rather than forcing information "
        "- You recognize conversation patterns and adapt your style to match user engagement "
        "- You provide concise, helpful responses (2-3 sentences) that respect the user's interest level "
        "- You never push unwanted information or random facts unless the user explicitly requests them"
    )
    
    @staticmethod
    def qa_template(
        question: str,
        kg_context: str,
        embedding_context: Optional[str] = None,
        ml_context: Optional[str] = None,
        persona: str = "conversational_fpl",
        data_scope: Optional[str] = None,
        is_first_message: bool = False,
        chat_history: Optional[List[Dict[str, str]]] = None,
        model_key: Optional[str] = None
    ) -> str:
        """
        Template for Q&A responses.
        
        Args:
            question: User's question
            kg_context: Context from Cypher queries
            embedding_context: Optional context from embedding search
            ml_context: Optional context from ML prediction engine (XGBoost forecasts)
            persona: Persona key
            data_scope: Description of the data scope (e.g., "all seasons (...)" or "2025-26 season")
            chat_history: Previous conversation messages for context
            model_key: The model being used (to customize prompt)
        """
        # Use enhanced persona for Llama 3.3 70B
        if model_key in ("gpt-oss-120b", "gpt-oss-20b", "llama-3.3-70b"):
            persona_text = PromptTemplates.PERSONAS.get("conversational_fpl_llama", PromptTemplates.PERSONAS["conversational_fpl"])
        else:
            persona_text = PromptTemplates.PERSONAS.get(persona, PromptTemplates.PERSONAS["conversational_fpl"])
        
        # Add data scope information with strong emphasis
        scope_text = ""
        if data_scope:
            if "all seasons" in data_scope.lower():
                scope_text = f"""
**⚠️ CRITICAL - Data Scope**: This data covers {data_scope}.
**ALL STATISTICS BELOW ARE COMBINED/TOTAL ACROSS ALL {_N} SEASONS ({_ALL_SEASONS})**
When you mention ANY statistic, you MUST say "across all seasons ({_FIRST} through {_LAST})" or "combined total across {_N} seasons".
NEVER quote a number without this qualifier. A player's combined goals across {_N} seasons is NOT their goals in one season.
The {CURRENT_SEASON} season is still in progress, so its numbers are "so far this season".
"""
            else:
                scope_text = f"""
**⚠️ CRITICAL - Data Scope**: This data covers ONLY {data_scope}.
**ALL STATISTICS BELOW ARE FOR THE {data_scope.upper()} SEASON ONLY — NOT COMBINED ACROSS MULTIPLE SEASONS**
When you mention ANY statistic, you MUST say "in the {data_scope} season".
NEVER quote a number from this data as an all-time or multi-season total.
Example: Say "Salah scored 19 goals in the 2022-23 season" NOT "Salah scored 64 goals" (which would be wrong).
"""
        
        # Strong directive about greeting behavior and explicit flag
        greeting_directive = (
            "IMPORTANT: Never begin your response with a salutation or greeting (e.g., 'Hi', 'Hey', 'Hello') UNLESS the 'FirstMessage' flag below is True. "
            "If 'FirstMessage' is True you may include a single brief friendly greeting at the start; otherwise go straight to the answer."
        )
        
        # Format conversation history
        history_text = ""
        if chat_history and len(chat_history) > 0:
            history_text = "\n### Conversation History:\n"
            # Only include last 5 messages to avoid context bloat
            recent_history = chat_history[-5:]
            for msg in recent_history:
                role = "User" if msg.get("role") == "user" else "Assistant"
                content = msg.get("content", "")
                history_text += f"{role}: {content}\n"
            history_text += "\n**IMPORTANT**: When the user says 'him', 'her', 'them', or 'that player', they are referring to a player mentioned in the conversation history above. Use the conversation context to resolve these references.\n"

        template = f"""{persona_text}
{greeting_directive}
FirstMessage: {str(is_first_message)}
{history_text}
### Knowledge Graph Data (Historical Stats):
{scope_text}{kg_context}
"""
        
        if embedding_context:
            template += f"""
### Similar Players (Semantic Search):
{embedding_context}
"""

        if ml_context:
            template += f"""
### ML Predictions (XGBoost Forecast):
{ml_context}
"""
        
        template += f"""
### User Question:
{question}

### Instructions:
**CRITICAL - SEASON CLARITY:**
- ALWAYS explicitly state which season(s) the statistics are from
- If the data scope says "2025-26 season", ALL numbers you mention are ONLY for that season
- If the data scope says "all seasons", the numbers are COMBINED across all {_N} seasons ({_ALL_SEASONS})
- The {CURRENT_SEASON} season is IN PROGRESS: say "so far this season" for its numbers
- NEVER say "Harry Kane scored 70 goals" without saying "across all seasons" or "in the 2022-23 season"
- Format: "[Player] scored [X] goals in [specific season]" OR "[Player] scored [X] goals across all seasons ({_FIRST} through {_LAST})"

**ML FORECAST RULES:**
- If "ML Predictions" are provided above, those are forecasts for the NEXT GAMEWEEK of the current {CURRENT_SEASON} season.
- ML predictions are forecasts based on recent form and upcoming fixtures - they are NOT historical stats.
- When asked about "next gameweek" or "upcoming matches" or "who to pick", ALWAYS use ML Predictions if available.
- Always distinguish between "Historical Stats" (from Knowledge Graph) and "ML Forecasts" (for next gameweek).
- When discussing recent form for context, prefer {CURRENT_SEASON} stats so far; {_PREV_SEASON} is the most recent complete season.
- Example: "Haaland has 5 goals so far in {CURRENT_SEASON}, and our ML model predicts 8.0 points in his next match vs Bournemouth."
- If ML Predictions show Haaland with 8.03 predicted points, that is THE answer when asked "who should I pick" or "how many points will he get".
- NEVER ignore ML Predictions when they are present - they are the most important data for future performance questions.
- A ranked ML list is in order, best first. When you name a second choice or an alternative, it is the next entry in that order; for a position ("a midfield option"), it is that position's highest-ranked entry. Never skip a higher-ranked player for a better-known one, and never call a player "next" when someone is ranked between them.

**UNDERSTANDING USER INTENT:**
- Vague responses like "hmm", "i dont know", "not sure", "maybe" mean the user is UNCERTAIN - offer helpful choices
- When user is uncertain, DON'T force information - instead say: "Would you like to hear about [specific option A] or [specific option B]?"
- Only share additional facts when the user explicitly shows interest or asks follow-up questions
- If user says "I don't care" or "no thanks", acknowledge and ask what they'd prefer to know about instead

**DATA USAGE RULES:**
- Use ONLY the exact data provided in "Knowledge Graph Data" and "ML Predictions" above
- The data is NUMBERED and SORTED - entry #1 is the TOP/BEST result
- When asked "who is the most/top/best", answer with entry #1 from the data
- Quote EXACT numbers from the data - never make up statistics
- DO NOT hallucinate or use training knowledge - ONLY use the data above

**RESPONSE GUIDELINES:**
- Keep responses SHORT (2-3 sentences max unless user asks for more detail)
- When user is vague/uncertain: Offer 2-3 specific choices and let THEM decide
- When user is engaged: Provide the requested information with relevant context
- When user shows disinterest: Ask what they'd prefer to know about instead
- NEVER push unwanted comparisons or random facts unless explicitly requested

**GOOD RESPONSE EXAMPLES:**
User: "hmm i dont know"
Good: "No worries! Would you like to hear about top midfielders, defenders with the most assists, or something else?"
Bad: "Trent Alexander-Arnold is a standout defender with..." (forcing information)

User: "tell me more"
Good: "He scored 36 goals across 35 games in the 2022-23 season..." (clearly states season)

User: "who scored the most?"
Good: "Erling Haaland with 36 goals in the 2022-23 season." (direct answer with season specified)

User: "who will score next gameweek?" or "who should I pick?"
IF ML PREDICTIONS ARE AVAILABLE:
Good: "Based on our ML model, Haaland is forecast for 8.0 points in his next match vs Bournemouth (Home). He's the top predicted performer."
Bad: "Haaland scored 36 goals in the 2022-23 season." (WRONG - giving historical stats instead of predictions)

User: "how many points do you think he's gonna earn?" (after discussing a player)
IF ML PREDICTIONS ARE AVAILABLE:
Good: "Our ML model forecasts Haaland will score 8.0 points in his next match vs Bournemouth at home."
Bad: "Based on historical stats, Haaland scored 36 goals in 2022-23..." (WRONG - giving old season stats instead of prediction)

User: "forecasted points" or "predicted points"
IF ML PREDICTIONS ARE AVAILABLE:
Good: "Haaland: 8.0 predicted points vs Bournemouth (H), then entry #2 with his own points and opponent" (the players in the list's order, with their exact numbers)
Bad: "Haaland's forecasted points are not explicitly listed..." (WRONG if they ARE in ML Predictions section above)

User: "compare him to other top scorers"
IF DATA SCOPE IS "2025-26 season":
Good: "In the 2025-26 season specifically, Harry Kane scored 30 goals and Son scored 10 goals."
Bad: "Harry Kane scored 70 goals" (WRONG - this is a combined total, not one season)

IF DATA SCOPE IS "all seasons":
Good: "Across all {_N} seasons ({_FIRST} through {_LAST}), Harry Kane scored 70 goals total and Son scored 40 goals total."
Bad: "Harry Kane scored 70 goals" (WRONG - doesn't clarify it's combined)

### Answer:"""
        
        return template
    
    @staticmethod
    def trivia_template(
        question: str,
        options: List[str],
        kg_context: str,
        difficulty: str = "medium"
    ) -> str:
        """
        Template for trivia question assistance.
        """
        template = f"""{PromptTemplates.PERSONAS['trivia_master']}

### Trivia Question ({difficulty.upper()} difficulty):
{question}

### Options:
{chr(10).join(f'{chr(65+i)}. {opt}' for i, opt in enumerate(options))}

### Available Data:
{kg_context}

### Instructions:
1. Identify the correct answer from the options
2. Explain why it's correct with specific data
3. Briefly mention why other options are incorrect
4. Add an interesting related fact if possible

### Response:"""
        
        return template
    
    @staticmethod
    def comparison_template(
        player1: str,
        player2: str,
        kg_context: str,
        comparison_type: str = "general"
    ) -> str:
        """
        Template for player comparisons.
        """
        template = f"""{PromptTemplates.PERSONAS['stats_analyst']}

### Player Comparison: {player1} vs {player2}

### Statistics:
{kg_context}

### Comparison Focus: {comparison_type.replace('_', ' ').title()}

### Instructions:
1. Compare both players across key metrics
2. Highlight strengths and weaknesses of each
3. Consider value for money (points per million)
4. Provide a clear recommendation with reasoning
5. Format your response with clear sections

### Analysis:"""
        
        return template
    
    @staticmethod
    def recommendation_template(
        request: str,
        kg_context: str,
        budget: Optional[float] = None,
        position: Optional[str] = None,
        num_picks: int = 3
    ) -> str:
        """
        Template for player recommendations.
        """
        constraints = []
        if budget:
            constraints.append(f"Budget: £{budget}m")
        if position:
            position_names = {"GK": "Goalkeeper", "DEF": "Defender", "MID": "Midfielder", "FWD": "Forward"}
            constraints.append(f"Position: {position_names.get(position, position)}")
        
        constraint_text = "\n".join(constraints) if constraints else "No specific constraints"
        
        template = f"""{PromptTemplates.PERSONAS['transfer_advisor']}

### Request:
{request}

### Constraints:
{constraint_text}

### Available Player Data:
{kg_context}

### Instructions:
1. Recommend {num_picks} players that best fit the request
2. For each player, explain:
   - Key statistics supporting the pick
   - Recent form and consistency
   - Value proposition (points per million)
   - Any risks or concerns
3. Rank recommendations by priority
4. Consider team balance if mentioned

### Recommendations:"""
        
        return template
    
    @staticmethod
    def summary_template(
        topic: str,
        kg_context: str,
        time_period: str = "season"
    ) -> str:
        """
        Template for generating summaries.
        """
        template = f"""{PromptTemplates.PERSONAS['conversational_fpl']}

### Topic: {topic}
### Period: {time_period}

### Data:
{kg_context}

### Instructions:
1. Provide a comprehensive summary of the topic
2. Highlight key statistics and trends
3. Identify standout performers
4. Note any interesting patterns or anomalies
5. Keep the summary engaging and informative

### Summary:"""
        
        return template
    
    @staticmethod
    def error_handling_template(
        original_query: str,
        error_type: str
    ) -> str:
        """
        Template for handling errors gracefully.
        """
        error_messages = {
            "no_data": "I couldn't find specific data for your query in the knowledge graph.",
            "ambiguous": "Your query seems ambiguous. Could you be more specific?",
            "invalid_entity": "I couldn't identify the player or team you mentioned.",
            "no_context": "I don't have enough context to answer this question accurately."
        }
        
        message = error_messages.get(error_type, "I encountered an issue processing your request.")
        
        template = f"""I apologize, but {message}

### Your Question:
{original_query}

### Suggestions:
- Try specifying a season (e.g., '2022-23')
- Use full player names when possible
- Ask about specific statistics (goals, assists, points)
- Mention the team or position if relevant

### Example Queries:
- "Who was the top scorer in 2025-26?"
- "Compare Mohamed Salah and Erling Haaland"
- "Best value midfielders last season"
- "How many clean sheets did Arsenal keep?"

Would you like to try rephrasing your question?"""
        
        return template
    
    @staticmethod
    def hybrid_context_template(
        question: str,
        cypher_context: str,
        embedding_context: str,
        retrieval_method: str = "hybrid"
    ) -> str:
        """
        Template that combines both retrieval methods.
        """
        template = f"""{PromptTemplates.PERSONAS['conversational_fpl']}

### Retrieval Method: {retrieval_method.upper()}

### Structured Query Results (Cypher):
{cypher_context}

### Semantic Search Results (Embeddings):
{embedding_context}

### Question:
{question}

### Instructions:
1. Consider information from BOTH retrieval methods
2. Prioritize structured data for specific statistics
3. Use semantic results for context and similar players
4. Clearly state which source supports your answer
5. Note any discrepancies between sources

### Answer:"""
        
        return template