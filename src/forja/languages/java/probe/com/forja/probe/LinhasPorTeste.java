package com.forja.probe;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import org.jacoco.core.analysis.Analyzer;
import org.jacoco.core.analysis.CoverageBuilder;
import org.jacoco.core.analysis.IClassCoverage;
import org.jacoco.core.analysis.ILine;
import org.jacoco.core.data.ExecutionDataStore;
import org.jacoco.core.tools.ExecFileLoader;

/** args: classesDir execDir sourceRoot out.json. One .exec per test unit, named unit#n.exec. */
public final class LinhasPorTeste {
    public static void main(String[] args) throws IOException {
        File classes = new File(args[0]);
        File[] execs = new File(args[1]).listFiles((d, n) -> n.endsWith(".exec"));
        if (execs == null) {
            execs = new File[0];
        }
        String raiz = args[2];
        Map<String, Map<Integer, Set<String>>> porArquivo = new TreeMap<>();
        for (File exec : execs) {
            String unidade = exec.getName().substring(0, exec.getName().lastIndexOf('#'));
            ExecFileLoader loader = new ExecFileLoader();
            try (InputStream in = new FileInputStream(exec)) {
                loader.load(in);
            }
            ExecutionDataStore store = loader.getExecutionDataStore();
            CoverageBuilder builder = new CoverageBuilder();
            new Analyzer(store, builder).analyzeAll(classes);
            for (IClassCoverage cc : builder.getClasses()) {
                String arquivo = raiz + "/" + cc.getPackageName().replace('.', '/') + "/" + cc.getSourceFileName();
                Map<Integer, Set<String>> linhas = porArquivo.computeIfAbsent(arquivo, k -> new TreeMap<>());
                for (int l = cc.getFirstLine(); l <= cc.getLastLine(); l++) {
                    ILine linha = cc.getLine(l);
                    if (linha.getInstructionCounter().getCoveredCount() > 0) {
                        linhas.computeIfAbsent(l, k -> new TreeSet<>()).add(unidade);
                    }
                }
            }
        }
        StringBuilder json = new StringBuilder("{\n");
        int a = 0;
        for (Map.Entry<String, Map<Integer, Set<String>>> arq : porArquivo.entrySet()) {
            json.append(a++ > 0 ? ",\n" : "").append("  \"").append(arq.getKey()).append("\": {");
            int l = 0;
            for (Map.Entry<Integer, Set<String>> linha : arq.getValue().entrySet()) {
                if (linha.getValue().isEmpty()) {
                    continue;
                }
                json.append(l++ > 0 ? ", " : "").append("\"").append(linha.getKey()).append("\": [");
                int t = 0;
                for (String id : linha.getValue()) {
                    json.append(t++ > 0 ? ", " : "").append("\"").append(id).append("\"");
                }
                json.append("]");
            }
            json.append("}");
        }
        json.append("\n}\n");
        java.nio.file.Files.writeString(new File(args[3]).toPath(), json.toString());
    }
}
