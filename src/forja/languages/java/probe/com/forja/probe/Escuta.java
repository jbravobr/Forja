package com.forja.probe;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import org.jacoco.agent.rt.RT;
import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.engine.support.descriptor.MethodSource;
import org.junit.platform.launcher.TestExecutionListener;
import org.junit.platform.launcher.TestIdentifier;

/** Per test: clears the JaCoCo agent data before it, and writes what the test hit after it. */
public final class Escuta implements TestExecutionListener {
    private static int contador;

    @Override
    public void executionStarted(TestIdentifier id) {
        if (id.isTest() && System.getProperty("forja.execdir") != null) {
            RT.getAgent().reset();
        }
    }

    @Override
    public void executionFinished(TestIdentifier id, TestExecutionResult result) {
        if (!id.isTest()) {
            return;
        }
        if (result.getStatus() == TestExecutionResult.Status.FAILED) {
            ParadaExtension.FALHOU.set(true);
        }
        String dir = System.getProperty("forja.execdir");
        if (dir == null) {
            return;
        }
        String unidade = id.getSource()
                .filter(MethodSource.class::isInstance)
                .map(s -> ((MethodSource) s).getClassName() + "." + ((MethodSource) s).getMethodName())
                .orElse("sem-metodo");
        synchronized (Escuta.class) {
            contador++;
            try {
                Files.write(new File(dir, unidade + "#" + contador + ".exec").toPath(),
                        RT.getAgent().getExecutionData(true));
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }
    }
}
