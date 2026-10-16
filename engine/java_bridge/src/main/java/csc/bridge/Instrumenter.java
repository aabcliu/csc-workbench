package csc.bridge;

import com.github.javaparser.JavaParser;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.comments.Comment;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.visitor.ModifierVisitor;
import com.github.javaparser.ast.visitor.Visitable;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.util.*;

/**
 * Code instrumentation for execution path tracing.
 *
 * Injects the probes needed by the expanded CSC runtime:
 *   - Function inputs
 *   - CSCTrace JSONL events for assignments, returns, branch conditions,
 *     loop conditions, and ternary conditions
 *   - Return values
 *   - Null pointer state
 *   - Cross-method PARAM_MAP and RETURN_VALUE bindings
 */
public class Instrumenter {

    // ======================== Main entry ========================

    /**
     * Add the active CSC instrumentation to a Java program.
     * Also applies cross-method compatibility instrumentation if multiple
     * methods are detected.
     */
    public static String addPrintStmt(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        cu.getAllContainedComments().forEach(Comment::remove);
        applyCSCTraceValueProbes(cu);
        applyCSCTraceConditionProbes(cu);
        String c1 = cu.toString();
        String c2 = addPrintStmtAtMethodBegin(c1);
        String c4 = c2;

        boolean isMultiMethod = hasMultipleMethods(code);
        String c5 = c4;

        String c6 = c5;
        String c7 = c6;
        String c8 = c7;
        String c9 = addPrintStmtForNullPointer(c8);

        // Cross-method instrumentation
        if (isMultiMethod) {
            c9 = instrumentMethodCalls(c9);
        }

        return c9;
    }

    // ======================== CSCTrace value probes ========================

    public static String addCSCTraceProbes(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        applyCSCTraceValueProbes(cu);
        applyCSCTraceConditionProbes(cu);
        return cu.toString();
    }

    public static String addCSCTraceValueProbes(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        applyCSCTraceValueProbes(cu);
        return cu.toString();
    }

    private static void applyCSCTraceValueProbes(CompilationUnit cu) {
        cu.accept(new ModifierVisitor<Void>() {
            @Override
            public Visitable visit(VariableDeclarator declarator, Void arg) {
                declarator.getInitializer().ifPresent(initializer -> {
                    if (!isCSCTraceValueCall(initializer)) {
                        declarator.setInitializer(buildAssignProbe(
                                declarator,
                                "var_decl",
                                declarator.getNameAsString(),
                                initializer,
                                declarator.getType().toString()));
                    }
                });
                return super.visit(declarator, arg);
            }

            @Override
            public Visitable visit(AssignExpr assign, Void arg) {
                if (!assign.getTarget().isNameExpr() || isCSCTraceValueCall(assign.getValue())) {
                    return super.visit(assign, arg);
                }

                if (assign.getOperator() == AssignExpr.Operator.ASSIGN) {
                    assign.setValue(buildAssignProbe(
                            assign,
                            "assign",
                            assign.getTarget().toString(),
                            assign.getValue(),
                            findVariableType(assign, assign.getTarget().toString()).orElse(null)));
                } else if (isSupportedCompoundAssignment(assign.getOperator())) {
                    Expression rhs = buildCompoundAssignmentRhs(assign);
                    assign.setOperator(AssignExpr.Operator.ASSIGN);
                    assign.setValue(buildAssignProbe(
                            assign,
                            "assign",
                            assign.getTarget().toString(),
                            rhs,
                            findVariableType(assign, assign.getTarget().toString()).orElse(null)));
                }
                return super.visit(assign, arg);
            }

            @Override
            public Visitable visit(UnaryExpr unary, Void arg) {
                if (isSupportedStandaloneUnaryUpdate(unary)) {
                    return buildUnaryUpdateAssignment(unary);
                }
                return super.visit(unary, arg);
            }

            @Override
            public Visitable visit(ReturnStmt stmt, Void arg) {
                stmt.getExpression().ifPresent(value -> {
                    if (!isCSCTraceValueCall(value)) {
                        stmt.setExpression(buildReturnProbe(stmt, value));
                    }
                });
                return super.visit(stmt, arg);
            }
        }, null);
    }

    private static boolean isSupportedCompoundAssignment(AssignExpr.Operator operator) {
        return operator == AssignExpr.Operator.PLUS
                || operator == AssignExpr.Operator.MINUS
                || operator == AssignExpr.Operator.MULTIPLY
                || operator == AssignExpr.Operator.DIVIDE
                || operator == AssignExpr.Operator.REMAINDER;
    }

    private static Expression buildCompoundAssignmentRhs(AssignExpr assign) {
        BinaryExpr.Operator operator = compoundToBinaryOperator(assign.getOperator());
        return new BinaryExpr(assign.getTarget().clone(), assign.getValue().clone(), operator);
    }

    private static BinaryExpr.Operator compoundToBinaryOperator(AssignExpr.Operator operator) {
        switch (operator) {
            case PLUS:
                return BinaryExpr.Operator.PLUS;
            case MINUS:
                return BinaryExpr.Operator.MINUS;
            case MULTIPLY:
                return BinaryExpr.Operator.MULTIPLY;
            case DIVIDE:
                return BinaryExpr.Operator.DIVIDE;
            case REMAINDER:
                return BinaryExpr.Operator.REMAINDER;
            default:
                throw new IllegalArgumentException("Unsupported compound assignment: " + operator);
        }
    }

    private static boolean isSupportedStandaloneUnaryUpdate(UnaryExpr unary) {
        if (!isIncrementOrDecrement(unary.getOperator()) || !unary.getExpression().isNameExpr()) {
            return false;
        }

        return unary.getParentNode()
                .map(parent -> parent instanceof ExpressionStmt || isDirectForUpdate(unary, parent))
                .orElse(false);
    }

    private static boolean isIncrementOrDecrement(UnaryExpr.Operator operator) {
        return operator == UnaryExpr.Operator.POSTFIX_INCREMENT
                || operator == UnaryExpr.Operator.PREFIX_INCREMENT
                || operator == UnaryExpr.Operator.POSTFIX_DECREMENT
                || operator == UnaryExpr.Operator.PREFIX_DECREMENT;
    }

    private static boolean isDirectForUpdate(UnaryExpr unary, Node parent) {
        if (!(parent instanceof ForStmt)) {
            return false;
        }
        ForStmt forStmt = (ForStmt) parent;
        return forStmt.getUpdate().stream().anyMatch(update -> update == unary);
    }

    private static Expression buildUnaryUpdateAssignment(UnaryExpr unary) {
        Expression target = unary.getExpression().clone();
        BinaryExpr.Operator operator = isIncrement(unary.getOperator())
                ? BinaryExpr.Operator.PLUS
                : BinaryExpr.Operator.MINUS;
        Expression rhs = new BinaryExpr(unary.getExpression().clone(), new IntegerLiteralExpr(1), operator);
        Expression probe = buildAssignProbe(
                unary,
                "assign",
                unary.getExpression().toString(),
                rhs,
                findVariableType(unary, unary.getExpression().toString()).orElse(null));
        return new AssignExpr(target, probe, AssignExpr.Operator.ASSIGN);
    }

    private static boolean isIncrement(UnaryExpr.Operator operator) {
        return operator == UnaryExpr.Operator.POSTFIX_INCREMENT
                || operator == UnaryExpr.Operator.PREFIX_INCREMENT;
    }

    private static Expression buildAssignProbe(Node node, String kind, String target,
                                               Expression value, String targetType) {
        int line = node.getRange().map(range -> range.begin.line).orElse(0);
        String methodName = "char".equals(targetType) ? "assignChar" : "assignObject";
        Expression probeValue = "char".equals(targetType) ? castToChar(value) : value.clone();
        return new MethodCallExpr(
                new NameExpr("csc.bridge.CSCTrace"),
                methodName,
                NodeList.nodeList(
                        new IntegerLiteralExpr(line),
                        new StringLiteralExpr(kind),
                        new StringLiteralExpr(target),
                        new StringLiteralExpr(value.toString()),
                        probeValue
                ));
    }

    private static Expression buildReturnProbe(Node node, Expression value) {
        int line = node.getRange().map(range -> range.begin.line).orElse(0);
        String returnType = node.findAncestor(MethodDeclaration.class)
                .map(method -> method.getType().toString())
                .orElse(null);
        String methodName = "char".equals(returnType) ? "retChar" : "retObject";
        Expression probeValue = "char".equals(returnType) ? castToChar(value) : value.clone();
        return new MethodCallExpr(
                new NameExpr("csc.bridge.CSCTrace"),
                methodName,
                NodeList.nodeList(
                        new IntegerLiteralExpr(line),
                        new StringLiteralExpr("return_value"),
                        new StringLiteralExpr(value.toString()),
                        probeValue
                ));
    }

    private static CastExpr castToChar(Expression value) {
        return new CastExpr(StaticJavaParser.parseType("char"), new EnclosedExpr(value.clone()));
    }

    private static Optional<String> findVariableType(Node node, String variableName) {
        Optional<MethodDeclaration> methodOpt = node.findAncestor(MethodDeclaration.class);
        if (methodOpt.isPresent()) {
            MethodDeclaration method = methodOpt.get();
            for (Parameter param : method.getParameters()) {
                if (param.getNameAsString().equals(variableName)) {
                    return Optional.of(param.getType().toString());
                }
            }
        }

        Optional<BlockStmt> blockOpt = node.findAncestor(BlockStmt.class);
        if (blockOpt.isPresent()) {
            List<VariableDeclarator> declarations = blockOpt.get().findAll(VariableDeclarator.class);
            for (VariableDeclarator declaration : declarations) {
                if (declaration.getNameAsString().equals(variableName)) {
                    Type type = declaration.getType();
                    return Optional.of(type.toString());
                }
            }
        }

        Optional<CompilationUnit> cuOpt = node.findCompilationUnit();
        if (cuOpt.isPresent()) {
            List<VariableDeclarator> declarations = cuOpt.get().findAll(VariableDeclarator.class);
            for (VariableDeclarator declaration : declarations) {
                if (declaration.getNameAsString().equals(variableName)) {
                    Type type = declaration.getType();
                    return Optional.of(type.toString());
                }
            }
        }

        return Optional.empty();
    }

    private static boolean isCSCTraceValueCall(Expression expr) {
        if (!expr.isMethodCallExpr()) {
            return false;
        }
        MethodCallExpr call = expr.asMethodCallExpr();
        String name = call.getNameAsString();
        String scope = call.getScope().map(Object::toString).orElse("");
        return scope.equals("csc.bridge.CSCTrace")
                && (name.startsWith("assign") || name.startsWith("ret"));
    }

    // ======================== CSCTrace condition probes ========================

    public static String addCSCTraceConditionProbes(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        applyCSCTraceConditionProbes(cu);
        return cu.toString();
    }

    private static void applyCSCTraceConditionProbes(CompilationUnit cu) {
        cu.accept(new ModifierVisitor<Void>() {
            @Override
            public Visitable visit(IfStmt ifStmt, Void arg) {
                ifStmt.setCondition(wrapDecisionExpression(ifStmt.getCondition(), "if"));
                return super.visit(ifStmt, arg);
            }

            @Override
            public Visitable visit(WhileStmt whileStmt, Void arg) {
                whileStmt.setCondition(wrapDecisionExpression(whileStmt.getCondition(), "while"));
                return super.visit(whileStmt, arg);
            }

            @Override
            public Visitable visit(ForStmt forStmt, Void arg) {
                if (forStmt.getCompare().isPresent()) {
                    forStmt.setCompare(wrapDecisionExpression(forStmt.getCompare().get(), "for"));
                }
                return super.visit(forStmt, arg);
            }

            @Override
            public Visitable visit(DoStmt doStmt, Void arg) {
                doStmt.setCondition(wrapDecisionExpression(doStmt.getCondition(), "do"));
                return super.visit(doStmt, arg);
            }

            @Override
            public Visitable visit(ConditionalExpr expr, Void arg) {
                expr.setCondition(wrapDecisionExpression(expr.getCondition(), "ternary"));
                return super.visit(expr, arg);
            }
        }, null);
    }

    private static Expression wrapDecisionExpression(Expression expr, String kind) {
        if (isCSCTraceBranchCall(expr)) {
            return expr;
        }
        Expression original = expr.clone();
        String decisionId = probeId("branch", kind, original);
        int line = original.getRange().map(range -> range.begin.line).orElse(0);
        Expression atoms = wrapBooleanExpression(original.clone(), kind, new int[]{1});
        return new MethodCallExpr(
                new NameExpr("csc.bridge.CSCTrace"),
                "branch",
                NodeList.nodeList(
                        new StringLiteralExpr(decisionId),
                        new IntegerLiteralExpr(line),
                        new StringLiteralExpr(kind),
                        new StringLiteralExpr(original.toString()),
                        atoms
                ));
    }

    private static Expression wrapBooleanExpression(Expression expr, String kind, int[] order) {
        if (isCSCTraceCondCall(expr)) {
            return expr;
        }

        if (expr.isEnclosedExpr()) {
            EnclosedExpr enclosed = expr.asEnclosedExpr();
            enclosed.setInner(wrapBooleanExpression(enclosed.getInner(), kind, order));
            return enclosed;
        }

        if (expr.isUnaryExpr()
                && expr.asUnaryExpr().getOperator() == UnaryExpr.Operator.LOGICAL_COMPLEMENT) {
            UnaryExpr unary = expr.asUnaryExpr();
            unary.setExpression(wrapBooleanExpression(unary.getExpression(), kind, order));
            return unary;
        }

        if (expr.isBinaryExpr()) {
            BinaryExpr binary = expr.asBinaryExpr();
            BinaryExpr.Operator op = binary.getOperator();
            if (op == BinaryExpr.Operator.AND || op == BinaryExpr.Operator.OR) {
                binary.setLeft(wrapBooleanExpression(binary.getLeft(), kind, order));
                binary.setRight(wrapBooleanExpression(binary.getRight(), kind, order));
                return binary;
            }
        }

        return buildCondProbe(expr, kind, order[0]++);
    }

    private static Expression buildCondProbe(Expression expr, String kind, int order) {
        int line = expr.getRange().map(range -> range.begin.line).orElse(0);
        return new MethodCallExpr(
                new NameExpr("csc.bridge.CSCTrace"),
                "cond",
                NodeList.nodeList(
                        new StringLiteralExpr(probeId("condition", kind, expr)),
                        new IntegerLiteralExpr(line),
                        new StringLiteralExpr(kind),
                        new IntegerLiteralExpr(order),
                        new StringLiteralExpr(expr.toString()),
                        expr.clone()
                ));
    }

    private static String probeId(String role, String kind, Node node) {
        return node.getRange()
                .map(range -> role + ":" + kind + ":"
                        + range.begin.line + ":" + range.begin.column + ":"
                        + range.end.line + ":" + range.end.column)
                .orElse(role + ":" + kind + ":unknown");
    }

    private static boolean isCSCTraceCondCall(Expression expr) {
        if (!expr.isMethodCallExpr()) {
            return false;
        }
        MethodCallExpr call = expr.asMethodCallExpr();
        return call.getNameAsString().equals("cond")
                && call.getScope().map(Object::toString).orElse("").equals("csc.bridge.CSCTrace");
    }

    private static boolean isCSCTraceBranchCall(Expression expr) {
        if (!expr.isMethodCallExpr()) {
            return false;
        }
        MethodCallExpr call = expr.asMethodCallExpr();
        return call.getNameAsString().equals("branch")
                && call.getScope().map(Object::toString).orElse("").equals("csc.bridge.CSCTrace");
    }

    /** Build the static denominator used by branch and condition coverage. */
    public static String coverageCatalog(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        List<String> decisions = new ArrayList<>();
        List<String> conditions = new ArrayList<>();
        collectDecisionCatalog(cu, decisions, conditions);
        List<String> exclusions = collectCoverageExclusions(cu);
        return "{\n  \"format\": \"coverage.catalog\",\n  \"version\": 1,\n"
                + "  \"decisions\": [\n" + String.join(",\n", decisions) + "\n  ],\n"
                + "  \"conditions\": [\n" + String.join(",\n", conditions) + "\n  ],\n"
                + "  \"exclusions\": [\n" + String.join(",\n", exclusions) + "\n  ]\n}\n";
    }

    private static void collectDecisionCatalog(CompilationUnit cu, List<String> decisions,
                                               List<String> conditions) {
        List<Node> nodes = new ArrayList<>();
        nodes.addAll(cu.findAll(IfStmt.class));
        nodes.addAll(cu.findAll(WhileStmt.class));
        nodes.addAll(cu.findAll(ForStmt.class, stmt -> stmt.getCompare().isPresent()));
        nodes.addAll(cu.findAll(DoStmt.class));
        nodes.addAll(cu.findAll(ConditionalExpr.class));
        nodes.sort(Comparator.comparingInt((Node node) -> node.getRange().map(r -> r.begin.line).orElse(0))
                .thenComparingInt(node -> node.getRange().map(r -> r.begin.column).orElse(0)));
        for (Node node : nodes) {
            String kind;
            Expression expr;
            if (node instanceof IfStmt) { kind = "if"; expr = ((IfStmt) node).getCondition(); }
            else if (node instanceof WhileStmt) { kind = "while"; expr = ((WhileStmt) node).getCondition(); }
            else if (node instanceof ForStmt) { kind = "for"; expr = ((ForStmt) node).getCompare().get(); }
            else if (node instanceof DoStmt) { kind = "do"; expr = ((DoStmt) node).getCondition(); }
            else { kind = "ternary"; expr = ((ConditionalExpr) node).getCondition(); }
            decisions.add(catalogEntry(probeId("branch", kind, expr), kind, expr));
            List<Expression> atoms = new ArrayList<>();
            collectAtomicConditions(expr, atoms);
            for (Expression atom : atoms) {
                conditions.add(catalogEntry(probeId("condition", kind, atom), kind, atom));
            }
        }
    }

    private static void collectAtomicConditions(Expression expr, List<Expression> atoms) {
        if (expr.isEnclosedExpr()) {
            collectAtomicConditions(expr.asEnclosedExpr().getInner(), atoms);
        } else if (expr.isUnaryExpr()
                && expr.asUnaryExpr().getOperator() == UnaryExpr.Operator.LOGICAL_COMPLEMENT) {
            collectAtomicConditions(expr.asUnaryExpr().getExpression(), atoms);
        } else if (expr.isBinaryExpr()
                && (expr.asBinaryExpr().getOperator() == BinaryExpr.Operator.AND
                    || expr.asBinaryExpr().getOperator() == BinaryExpr.Operator.OR)) {
            collectAtomicConditions(expr.asBinaryExpr().getLeft(), atoms);
            collectAtomicConditions(expr.asBinaryExpr().getRight(), atoms);
        } else {
            atoms.add(expr);
        }
    }

    private static String catalogEntry(String id, String kind, Expression expr) {
        int line = expr.getRange().map(r -> r.begin.line).orElse(0);
        return "    {\"id\": \"" + jsonEscape(id) + "\", \"line\": " + line
                + ", \"kind\": \"" + jsonEscape(kind) + "\", \"expr\": \""
                + jsonEscape(expr.toString()) + "\"}";
    }

    private static List<String> collectCoverageExclusions(CompilationUnit cu) {
        List<String> exclusions = new ArrayList<>();
        for (SwitchStmt node : cu.findAll(SwitchStmt.class)) {
            exclusions.add(exclusionEntry(node, "switch", "switch_branch_not_instrumented"));
        }
        for (SwitchExpr node : cu.findAll(SwitchExpr.class)) {
            exclusions.add(exclusionEntry(node, "switch_expression", "switch_branch_not_instrumented"));
        }
        for (ForEachStmt node : cu.findAll(ForEachStmt.class)) {
            exclusions.add(exclusionEntry(node, "foreach", "implicit_iteration_branch_not_instrumented"));
        }
        return exclusions;
    }

    private static String exclusionEntry(Node node, String kind, String reason) {
        int line = node.getRange().map(r -> r.begin.line).orElse(0);
        return "    {\"line\": " + line + ", \"kind\": \"" + jsonEscape(kind)
                + "\", \"reason\": \"" + jsonEscape(reason) + "\"}";
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t");
    }

    // ======================== Multi-method detection ========================

    public static boolean hasMultipleMethods(String code) {
        try {
            CompilationUnit cu = new JavaParser().parse(code).getResult().get();
            List<MethodDeclaration> methods = cu.findAll(MethodDeclaration.class);
            long nonMainMethodCount = methods.stream()
                .filter(m -> !m.getNameAsString().equals("main"))
                .count();
            return nonMainMethodCount > 1;
        } catch (Exception e) {
            return false;
        }
    }

    // ======================== Comment removal ========================

    public static String removeAllComments(String code) {
        CompilationUnit cu = StaticJavaParser.parse(code);
        cu.getAllContainedComments().forEach(Comment::remove);
        return cu.toString();
    }

    // ======================== Method-begin instrumentation ========================

    public static String addPrintStmtAtMethodBegin(String code) {
        CompilationUnit cu = new JavaParser().parse(code).getResult().get();
        cu.accept(new ModifierVisitor<Void>() {
            @Override
            public Visitable visit(MethodDeclaration md, Void arg) {
                if (md.isStatic() && !md.getNameAsString().equals("main")) {
                    for (Parameter param : md.getParameters()) {
                        String type = param.getType().toString();
                        Statement printStmt = new ExpressionStmt(new MethodCallExpr(
                                new NameExpr("System.out"),
                                "println",
                                NodeList.nodeList(new BinaryExpr(
                                        new StringLiteralExpr("Function input " + type + " parameter " + param.getName() + " = "),
                                        new EnclosedExpr(new NameExpr(param.getNameAsString())),
                                        BinaryExpr.Operator.PLUS
                                ))
                        ));
                        if (md.getBody().isPresent()) {
                            BlockStmt body = md.getBody().get();
                            body.addStatement(0, printStmt);
                        }
                    }
                }
                return super.visit(md, arg);
            }
        }, null);
        return cu.toString();
    }

    // ======================== Null pointer detection ========================

    public static String addPrintStmtForNullPointer(String code) {
        CompilationUnit cu = StaticJavaParser.parse(code);
        cu.findAll(VariableDeclarationExpr.class).forEach(vde -> {
            if (!vde.getElementType().isPrimitiveType()) {
                for (VariableDeclarator var : vde.getVariables()) {
                    Optional<Expression> initOpt = var.getInitializer();
                    if (initOpt.isPresent()) {
                        Expression init = initOpt.get();
                        String varName = var.getNameAsString();
                        String rhsPrint;
                        if (init.isNullLiteralExpr()) {
                            rhsPrint = "false";
                        } else if (init.isNameExpr()) {
                            rhsPrint = init.asNameExpr().getNameAsString();
                        } else if (init.isObjectCreationExpr()) {
                            rhsPrint = "true";
                        } else {
                            rhsPrint = "true";
                        }
                        String printCode = String.format(
                                "System.out.println(\"NP detecting: %s = %s\");", varName, rhsPrint);
                        vde.getParentNode().ifPresent(parent -> {
                            if (parent instanceof ExpressionStmt) {
                                ExpressionStmt stmt = (ExpressionStmt) parent;
                                stmt.findAncestor(BlockStmt.class).ifPresent(block -> {
                                    int idx = block.getStatements().indexOf(stmt);
                                    block.addStatement(idx + 1, StaticJavaParser.parseStatement(printCode));
                                });
                            }
                        });
                    }
                }
            }
        });

        cu.findAll(AssignExpr.class).forEach(assign -> {
            Expression target = assign.getTarget();
            Expression value = assign.getValue();
            if (target.isNameExpr()) {
                String varName = target.asNameExpr().getNameAsString();
                boolean isReferenceVar = cu.findAll(VariableDeclarationExpr.class).stream()
                        .filter(vde -> vde.getVariables().stream()
                                .anyMatch(v -> v.getNameAsString().equals(varName)))
                        .anyMatch(vde -> !vde.getElementType().isPrimitiveType());
                if (!isReferenceVar) return;
                String rhsPrint;
                if (value.isNullLiteralExpr()) {
                    rhsPrint = "false";
                } else if (value.isNameExpr()) {
                    rhsPrint = value.asNameExpr().getNameAsString();
                } else if (value.isObjectCreationExpr()) {
                    rhsPrint = "true";
                } else {
                    rhsPrint = value.toString();
                }
                String printCode = String.format(
                        "System.out.println(\"NP detecting: %s = %s\");", varName, rhsPrint);
                assign.getParentNode().ifPresent(parent -> {
                    if (parent instanceof ExpressionStmt) {
                        ExpressionStmt stmt = (ExpressionStmt) parent;
                        stmt.findAncestor(BlockStmt.class).ifPresent(block -> {
                            int idx = block.getStatements().indexOf(stmt);
                            block.addStatement(idx + 1, StaticJavaParser.parseStatement(printCode));
                        });
                    }
                });
            }
        });
        return cu.toString();
    }

    // ======================== Cross-method call instrumentation ========================

    public static String instrumentMethodCalls(String code) {
        try {
            ParseResult<CompilationUnit> parseResult = new JavaParser().parse(code);
            if (!parseResult.isSuccessful()) return code;
            CompilationUnit cu = parseResult.getResult().get();
            List<MethodDeclaration> methods = cu.findAll(MethodDeclaration.class);
            Map<String, MethodDeclaration> methodMap = new HashMap<>();
            for (MethodDeclaration method : methods) {
                String key = method.getNameAsString() + "_" + method.getParameters().size();
                methodMap.put(key, method);
            }
            for (MethodDeclaration method : methods) {
                if (method.getNameAsString().equals("main")) continue;
                BlockStmt body = method.getBody().orElse(null);
                if (body == null) continue;
                List<Statement> originalStatements = new ArrayList<>(body.getStatements());
                body.getStatements().clear();
                for (Statement stmt : originalStatements) {
                    body.addStatement(instrumentStatement(stmt, methodMap));
                }
            }
            return cu.toString();
        } catch (Exception e) {
            System.err.println("Error in instrumentMethodCalls: " + e.getMessage());
            return code;
        }
    }

    private static Statement instrumentStatement(Statement stmt, Map<String, MethodDeclaration> methodMap) {
        if (stmt instanceof ExpressionStmt) {
            Expression expr = ((ExpressionStmt) stmt).getExpression();
            if (expr instanceof MethodCallExpr) {
                return instrumentMethodCall((ExpressionStmt) stmt, (MethodCallExpr) expr, methodMap);
            }
        }
        return stmt;
    }

    private static Statement instrumentMethodCall(ExpressionStmt originalStmt, MethodCallExpr call,
                                                   Map<String, MethodDeclaration> methodMap) {
        String methodName = call.getNameAsString();
        int argCount = call.getArguments().size();
        String key = methodName + "_" + argCount;
        if (!methodMap.containsKey(key)) return originalStmt;

        MethodDeclaration callee = methodMap.get(key);
        BlockStmt instrumentedBlock = new BlockStmt();

        // PARAM_MAP prints: trace parameter bindings
        for (int i = 0; i < argCount && i < callee.getParameters().size(); i++) {
            Parameter param = callee.getParameter(i);
            Expression arg = call.getArgument(i);
            String paramName = param.getNameAsString();
            String argStr = arg.toString();
            Statement paramPrint = new ExpressionStmt(new MethodCallExpr(
                    new NameExpr("System.out"), "println",
                    NodeList.nodeList(new BinaryExpr(
                            new StringLiteralExpr("PARAM_MAP: " + argStr + " -> " + paramName + ", current value of " + argStr + ": "),
                            new EnclosedExpr(new NameExpr(argStr)),
                            BinaryExpr.Operator.PLUS))));
            instrumentedBlock.addStatement(paramPrint);
        }

        instrumentedBlock.addStatement(originalStmt.clone());

        // RETURN_VALUE print if non-void
        if (!callee.getType().isVoidType()) {
            Statement retPrint = new ExpressionStmt(new MethodCallExpr(
                    new NameExpr("System.out"), "println",
                    NodeList.nodeList(new BinaryExpr(
                            new StringLiteralExpr("RETURN_VALUE: " + methodName + "() = return_value , current value of return_value : "),
                            new NameExpr("return_value"), BinaryExpr.Operator.PLUS))));
            instrumentedBlock.addStatement(retPrint);
        }

        return instrumentedBlock;
    }

    // ======================== Loop detection ========================

    public static boolean ssmpHasLoopStmt(String ssmp) {
        try {
            CompilationUnit cu = new JavaParser().parse(ssmp).getResult().get();
            MethodDeclaration md = cu.findFirst(MethodDeclaration.class).get();
            return containsLoop(md);
        } catch (Exception e) {
            return false;
        }
    }

    public static boolean containsLoop(MethodDeclaration method) {
        return method.getBody()
                .map(body -> body.getStatements().stream()
                        .anyMatch(Instrumenter::stmtHasLoopStmt))
                .orElse(false);
    }

    public static boolean stmtHasLoopStmt(Statement stmt) {
        if (isLoopStatement(stmt)) return true;
        boolean b = false;
        if (stmt instanceof IfStmt) {
            Statement thenStmt = ((IfStmt) stmt).getThenStmt();
            if (thenStmt instanceof BlockStmt) {
                for (Statement childStmt : ((BlockStmt) thenStmt).getStatements()) {
                    b = b || stmtHasLoopStmt(childStmt);
                }
            }
            if (b) return b;
            if (((IfStmt) stmt).getElseStmt().isPresent()) {
                b = b || stmtHasLoopStmt(((IfStmt) stmt).getElseStmt().get());
            }
        }
        if (stmt instanceof BlockStmt) {
            for (Statement childStmt : ((BlockStmt) stmt).getStatements()) {
                b = b || stmtHasLoopStmt(childStmt);
            }
        }
        return b;
    }

    public static boolean isLoopStatement(Statement stmt) {
        return stmt instanceof ForStmt || stmt instanceof WhileStmt ||
               stmt instanceof DoStmt || stmt instanceof ForEachStmt;
    }

    // ======================== CLI entry point ========================

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: java -jar csc-bridge.jar <input.java> [output.java] [coverage-catalog.json]");
            System.err.println("  If output.java is omitted, writes to stdout.");
            System.exit(1);
        }
        String code = java.nio.file.Files.readString(java.nio.file.Path.of(args[0]));
        String instrumented = addPrintStmt(code);
        if (args.length >= 2) {
            java.nio.file.Files.writeString(java.nio.file.Path.of(args[1]), instrumented);
            System.err.println("Instrumented code written to: " + args[1]);
        } else {
            System.out.println(instrumented);
        }
        if (args.length >= 3) {
            java.nio.file.Files.writeString(java.nio.file.Path.of(args[2]), coverageCatalog(code));
            System.err.println("Coverage catalog written to: " + args[2]);
        }
    }

    // ======================== Variable extraction from expressions ========================

    public static Set<String> extractVariablesInLogicalExpr(String javaExpression) throws Exception {
        String wrappedCode = "class Temp { void method() { boolean result = " + javaExpression + "; } }";
        CompilationUnit cu = new JavaParser().parse(wrappedCode).getResult().get();
        Set<String> variables = new HashSet<>();
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(NameExpr n, Void arg) {
                variables.add(n.getNameAsString());
                super.visit(n, arg);
            }
        }, null);
        return variables;
    }
}
