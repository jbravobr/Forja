package com.forja.probe;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.MethodOrdererContext;

/** Methods run in the order of the property forja.ordemMetodos (binary class name, a dot, the
 * method name: the ids without arguments); the others after. */
public final class OrdemDeMetodos implements MethodOrderer {
    @Override
    public void orderMethods(MethodOrdererContext context) {
        List<String> ordem = Arrays.asList(System.getProperty("forja.ordemMetodos", "").split(","));
        context.getMethodDescriptors().sort((a, b) -> Integer.compare(
                posicao(ordem, a.getMethod().getDeclaringClass().getName() + "." + a.getMethod().getName()),
                posicao(ordem, b.getMethod().getDeclaringClass().getName() + "." + b.getMethod().getName())));
    }

    private static int posicao(List<String> ordem, String nome) {
        int i = ordem.indexOf(nome);
        return i < 0 ? Integer.MAX_VALUE : i;
    }
}
