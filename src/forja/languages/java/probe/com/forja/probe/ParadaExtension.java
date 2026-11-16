package com.forja.probe;

import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.extension.ConditionEvaluationResult;
import org.junit.jupiter.api.extension.ExecutionCondition;
import org.junit.jupiter.api.extension.ExtensionContext;

/** When the property forja.parada is set, every test that starts after a failure is disabled. */
public final class ParadaExtension implements ExecutionCondition {
    static final AtomicBoolean FALHOU = new AtomicBoolean();

    @Override
    public ConditionEvaluationResult evaluateExecutionCondition(ExtensionContext context) {
        if (FALHOU.get() && System.getProperty("forja.parada") != null) {
            return ConditionEvaluationResult.disabled("parada: a test failed before");
        }
        return ConditionEvaluationResult.enabled("no failure yet");
    }
}
