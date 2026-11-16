package com.forja.probe;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.ClassOrderer;
import org.junit.jupiter.api.ClassOrdererContext;

/** Top-level classes run in the order of the property forja.ordem (binary names, as the ids);
 * the others after. */
public final class OrdemDeClasses implements ClassOrderer {
    @Override
    public void orderClasses(ClassOrdererContext context) {
        List<String> ordem = Arrays.asList(System.getProperty("forja.ordem", "").split(","));
        context.getClassDescriptors().sort((a, b) -> Integer.compare(
                posicao(ordem, a.getTestClass().getName()),
                posicao(ordem, b.getTestClass().getName())));
    }

    private static int posicao(List<String> ordem, String nome) {
        int i = ordem.indexOf(nome);
        return i < 0 ? Integer.MAX_VALUE : i;
    }
}
