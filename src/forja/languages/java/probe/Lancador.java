import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;
import javax.tools.ToolProvider;

/**
 * Run as {@code java Lancador.java <probe dir> <mode> <cache dir> <maven> <maven arguments...>}, with
 * the classpath of the probe's compilation in FORJA_PROBE_CLASSPATH. Compiles the probe into the
 * cache (one directory per content, in the Forja's own home, private to the user), then runs Maven
 * with that directory on the test classpath. In mode {@code linhas}, when Maven succeeds, it also
 * writes the tests of each line.
 */
public final class Lancador {
    private static final String REPORTS = ".forja-test-lines";

    public static void main(String[] args) throws Exception {
        Path fontes = Path.of(args[0]).toAbsolutePath();
        boolean linhas = "linhas".equals(args[1]);
        Path root = Path.of(args[2]).toAbsolutePath();
        List<String> maven = new ArrayList<>(Arrays.asList(args).subList(3, args.length));
        String classpath = System.getenv("FORJA_PROBE_CLASSPATH");
        if (classpath == null) {
            System.err.println("forja: FORJA_PROBE_CLASSPATH is not set");
            System.exit(2);
        }
        Path cache = compiled(fontes, root, classpath);
        if (cache == null) {
            System.exit(2);
        }
        if (linhas) {
            Files.createDirectories(Path.of(REPORTS, "exec"));
        }
        maven.add("-Dmaven.test.additionalClasspath=" + cache);
        int codigo = run(maven);
        if (linhas && codigo == 0) {
            codigo = run(List.of(
                    Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                    "-cp", cache + File.pathSeparator + classpath,
                    "com.forja.probe.LinhasPorTeste",
                    "target/classes", REPORTS + "/exec", "src/main/java", REPORTS + "/linhas.json"));
        }
        System.exit(codigo);
    }

    private static int run(List<String> command) throws IOException, InterruptedException {
        return new ProcessBuilder(command).inheritIO().start().waitFor();
    }

    /**
     * The directory holding the compiled probe, compiling it first when no trusted copy is there.
     * The root is created for the user only; a copy is trusted when this user owns it and nobody
     * else can write it. Compiling happens in a directory of its own, moved into place atomically.
     */
    private static Path compiled(Path fontes, Path root, String classpath) throws Exception {
        if (!Files.isDirectory(root)) {
            Files.createDirectories(root);
            restrict(root);
        }
        UserPrincipal me = ownerOfNewFile(root);
        List<Path> sources;
        try (Stream<Path> files = Files.walk(fontes)) {
            sources = files.filter(p -> p.toString().endsWith(".java")).sorted().toList();
        }
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        digest.update(classpath.getBytes(StandardCharsets.UTF_8));
        for (Path source : sources) {
            digest.update(fontes.relativize(source).toString().getBytes(StandardCharsets.UTF_8));
            digest.update(Files.readAllBytes(source));
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : digest.digest()) {
            hex.append(String.format("%02x", b));
        }
        Path cache = root.resolve(hex.substring(0, 32));
        if (trusted(cache, me)) {
            return cache;
        }
        Path work = Files.createTempDirectory(root, "compile-");
        List<String> arguments = new ArrayList<>(List.of("-encoding", "UTF-8", "--release", "17",
                "-cp", classpath, "-d", work.toString()));
        for (Path source : sources) {
            arguments.add(source.toString());
        }
        if (ToolProvider.getSystemJavaCompiler().run(null, null, null,
                arguments.toArray(String[]::new)) != 0) {
            System.err.println("forja: the probe does not compile");
            deleteTree(work);
            return null;
        }
        Path services = work.resolve("META-INF/services");
        Files.createDirectories(services);
        for (String service : List.of(
                "org.junit.jupiter.api.extension.Extension",
                "org.junit.platform.launcher.TestExecutionListener")) {
            Files.copy(fontes.resolve("META-INF/services").resolve(service),
                    services.resolve(service), StandardCopyOption.REPLACE_EXISTING);
        }
        Files.writeString(work.resolve("ok"), "");
        try {
            Files.move(work, cache, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            // another run put a copy in place first (on Windows that is an access error, not a
            // "already exists"): whether the copy in place is good is checked right below
            deleteTree(work);
        }
        if (!trusted(cache, me)) {
            System.err.println("forja: the probe's cache is not this user's: " + cache);
            return null;
        }
        return cache;
    }

    /** Exists, is complete, belongs to ``me`` and is not writable by its group or others. */
    private static boolean trusted(Path cache, UserPrincipal me) throws IOException {
        if (!Files.isDirectory(cache) || !Files.isRegularFile(cache.resolve("ok"))) {
            return false;
        }
        if (!Files.getOwner(cache).equals(me)) {
            return false;
        }
        if (isPosix(cache)) {
            Set<PosixFilePermission> permissions = Files.getPosixFilePermissions(cache);
            return !permissions.contains(PosixFilePermission.GROUP_WRITE)
                    && !permissions.contains(PosixFilePermission.OTHERS_WRITE);
        }
        return true;
    }

    /** The owner of a file made here, which is the user running Forja. */
    private static UserPrincipal ownerOfNewFile(Path root) throws IOException {
        Path probe = Files.createTempFile(root, "owner-", ".tmp");
        try {
            return Files.getOwner(probe);
        } finally {
            Files.delete(probe);
        }
    }

    private static void restrict(Path directory) throws IOException {
        if (isPosix(directory)) {
            Files.setPosixFilePermissions(directory, PosixFilePermissions.fromString("rwx------"));
        }
    }

    private static boolean isPosix(Path path) {
        return path.getFileSystem().supportedFileAttributeViews().contains("posix");
    }

    private static void deleteTree(Path directory) throws IOException {
        try (Stream<Path> files = Files.walk(directory)) {
            for (Path p : files.sorted((a, b) -> b.compareTo(a)).toList()) {
                Files.delete(p);
            }
        }
    }
}
