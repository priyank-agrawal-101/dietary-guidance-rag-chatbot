"""Only fixed application messages are returned before evidence retrieval."""

MESSAGES = {
    "medical": "I can't provide medical advice or a medically personalized diet. Please consult a qualified clinician or registered dietitian. Ask a separate question about general food selection or household food safety.",
    "target": "I can't recommend calorie allowances, weight targets, or what someone should weigh. Please consult a qualified clinician or registered dietitian. Ask a separate question about general dietary guidance or food safety.",
    "bypass": "I can't bypass the guidance scope or act as your clinician. Please consult a qualified clinician or registered dietitian for medical advice or calorie and weight targets.",
    "unrelated": "I focus on food and nutrition, so I can't answer that topic. I can help with food choices, cooking and storage safety, or understanding food labels.",
    "nutrients": "Individual-food nutrient values require a structured nutrient database, planned for Milestone 3. This capability is not available here, and I can't estimate those values from guidance documents. Ask a separate general guidance question.",
    "intent": "What would you like to know about food choices, cooking, storage, or food labels? A specific question will help me find the right guidance.",
    "food_storage": "Which food do you mean, and how long and at what temperature has it been stored or left out?",
    "selection": "Please use an unambiguous document name or a consistent explicit document selection.",
}
